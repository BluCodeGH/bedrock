# Interprets the minecraft bedrock world format.

import struct
import os.path
import numpy as np
import bedrock.leveldb as ldb
import bedrock.nbt as nbt

# Handles chunk loading and mapping blocks to chunks.
class World:
  def __init__(self, path):
    self.path = os.path.join(path, "db")
    self.db = None
    self.chunks = {}

  # Enable use in a with statement.
  def __enter__(self):
    self.db = ldb.open(self.path)
    return self

  def __exit__(self, *args):
    self.save()
    ldb.close(self.db)
    return False

  def getChunk(self, x, z):
    chunk = self.chunks.get((x, z), None)
    if chunk is None:
      chunk = Chunk(self.db, x, z)
      self.chunks[(x, z)] = chunk
    return chunk

  def getBlock(self, x, y, z):
    cx = x // 16
    x %= 16
    cz = z // 16
    z %= 16
    chunk = self.getChunk(cx, cz)
    return chunk.getBlock(x, y, z)

  def setBlock(self, x, y, z, block):
    cx = x // 16
    x %= 16
    cz = z // 16
    z %= 16
    chunk = self.getChunk(cx, cz)
    return chunk.setBlock(x, y, z, block)

  def save(self):
    for chunk in self.chunks.values():
      chunk.save(self.db)

# Handles biomes and tile entities. Maps blocks to subchunks.
class Chunk:
  def __init__(self, db, x, z):
    self.x = x
    self.z = z
    # Leveldb chunks are stored in a number of keys with the same prefix.
    self.keyBase = struct.pack("<ii", self.x, self.z)

    self.version = self._loadVersion(db)
    self.hMap, self.biomes = self._load2D(db)

    self.subchunks = []
    for i in range(16):
      try:
        self.subchunks.append(SubChunk(db, self.x, self.z, i)) #Pass off processing to the subchunk class
      #All the subchunks below an existing subchunk also exist, so we know we have reached the end.
      except KeyError:
        break

    self._loadTileEntities(db)

  # Version is simply a stored value.
  def _loadVersion(self, db):
    try:
      version = ldb.get(db, self.keyBase + b"v")
      version = struct.unpack("<B", version)[0]
      if version != 7:
        raise NotImplementedError("Unexpected chunk version {}.".format(version))
    except KeyError:
      raise ValueError("Chunk at {}, {} does not exist.".format(self.x, self.z))
    return version

  # Load heightmap (seemingly useless) and biome info
  def _load2D(self, db):
    data = ldb.get(db, self.keyBase + b'-')
    heightMap = struct.unpack("<" + "H" * 16 * 16, data[:2 * 16 * 16])
    biomes = struct.unpack("B" * 16 * 16, data[2 * 16 * 16:])
    return heightMap, biomes

  # Tile entities are stored as a bunch of NBT compound tags end to end.
  def _loadTileEntities(self, db):
    try:
      data = ldb.get(db, self.keyBase + b"1")
    except KeyError:
      return
    data = nbt.DataReader(data)
    while not data.finished():
      nbtData = nbt.decode(data)
      x = nbtData.pop("x").payload # We add back theses with the correct value on save, they are important.
      y = nbtData.pop("y").payload
      z = nbtData.pop("z").payload
      self.getBlock(x % 16, y, z % 16).nbt = nbtData

  def getBlock(self, x, y, z):
    if y // 16 + 1 > len(self.subchunks):
      return None
    return self.subchunks[y // 16].getBlock(x, y % 16, z)

  def setBlock(self, x, y, z, block):
    while y // 16 + 1 > len(self.subchunks):
      self.subchunks.append(SubChunk.empty(self.x, self.z, len(self.subchunks)))
    self.subchunks[y // 16].setBlock(x, y % 16, z, block)

  def save(self, db):
    version = struct.pack("<B", self.version)
    ldb.put(db, self.keyBase + b"v", version)
    self._save2D(db)
    for subchunk in self.subchunks:
      subchunk.save(db)
    self._saveTileEntities(db)

  def _save2D(self, db):
    pass

  def _saveTileEntities(self, db):
    data = nbt.DataWriter()
    for subchunk in self.subchunks:
      for x in range(16):
        for y in range(16):
          for z in range(16):
            block = subchunk.getBlock(x, y, z)
            if block.nbt is not None: # Add back the correct position.
              block.nbt.add(nbt.TAG_Int("x", subchunk.x * 16 + x))
              block.nbt.add(nbt.TAG_Int("y", subchunk.y * 16 + y))
              block.nbt.add(nbt.TAG_Int("z", subchunk.z * 16 + z))
              nbt.encode(block.nbt, data)
    ldb.put(db, self.keyBase + b"1", data.data)

  def __repr__(self):
    return "Chunk {} {}: {} subchunks".format(self.x, self.z, len(self.subchunks))

# Handles the blocks and block palette format.
class SubChunk:
  def __init__(self, db, x, z, y):
    self.x = x
    self.z = z
    self.y = y
    if db is not None: # For creating subchunks, there will be no DB.
      # Subchunks are stored as base key + subchunk key `/` + subchunk id (y level // 16)
      key = struct.pack("<iicB", x, z, b'/', y)
      data = ldb.get(db, key)
      self.version, data = data[0], data[1:]
      if self.version != 8:
        raise NotImplementedError("Unsupported subchunk version: {}".format(self.version))
      numStorages, data = data[0], data[1:]
      if numStorages != 1: # Maybe used for liquids
        raise NotImplementedError("Unexpected number of storages: {}".format(numStorages))

      blocks, data = self._loadBlocks(data)
      palette = self._loadPalette(data)

      self.blocks = np.empty(4096, dtype=Block) # Prepare with correct dtype
      for i, block in enumerate(blocks):
        block = palette[block]
        self.blocks[i] = Block(block["name"].payload, block["val"].payload) # .payload to get actual val

      self.blocks = self.blocks.reshape(16, 16, 16).swapaxes(1, 2) # Y and Z saved in an inverted order

  # These arent actual blocks, just ids pointing to the palette.
  def _loadBlocks(self, data):
    #Ignore LSB of data (its a flag) and get compacting level
    bitsPerBlock, data = data[0] >> 1, data[1:]
    blocksPerWord = 32 // bitsPerBlock # Word = 4 bytes, basis of compacting.
    numWords = - (-4096 // blocksPerWord) # Ceiling divide is inverted floor divide

    blockWords, data = struct.unpack("<" + "I" * numWords, data[:4 * numWords]), data[4 * numWords:]
    blocks = np.empty(4096, dtype=np.uint32)
    for i, word in enumerate(blockWords):
      for j in range(blocksPerWord):
        block = word & ((1 << bitsPerBlock) - 1) # Mask out number of bits for one block
        word >>= bitsPerBlock # For next iteration
        if i * blocksPerWord + j < 4096: # Safety net for padding at end.
          blocks[i * blocksPerWord + j] = block
    return blocks, data

  # NBT encoded block names (with minecraft:) and data values.
  def _loadPalette(self, data):
    palletLen, data = struct.unpack("<I", data[:4])[0], data[4:]
    dr = nbt.DataReader(data)
    palette = []
    for _ in range(palletLen):
      palette.append(nbt.decode(dr))
    return palette

  def getBlock(self, x, y, z):
    return self.blocks[x,y,z]

  def setBlock(self, x, y, z, block):
    self.blocks[x,y,z] = block

  def save(self, db):
    data = struct.pack("<BB", self.version, 1)
    palette, blockIDs = self._savePalette()
    data += self._saveBlocks(len(palette), blockIDs)
    data += struct.pack("<I", len(palette))
    for block in palette:
      data += nbt.encode(block)

    key = struct.pack("<iicB", self.x, self.z, b'/', self.y)
    ldb.put(db, key, data)

  # Compact blockIDs bitwise. See _loadBlocks for details.
  def _saveBlocks(self, paletteSize, blockIDs):
    bitsPerBlock = max(int(np.ceil(np.log2(paletteSize))), 1)
    blocksPerWord = 32 // bitsPerBlock
    numWords = - (-4096 // blocksPerWord)
    data = struct.pack("<B", bitsPerBlock << 1)

    for i in range(numWords):
      word = 0
      for j in range(blocksPerWord - 1, -1, -1):
        if i * blocksPerWord + j < 4096:
          word <<= bitsPerBlock
          word |= blockIDs[i * blocksPerWord + j]
      data += struct.pack("<I", word)
    return data

  # Make a palette, and get the block ids at the same time
  def _savePalette(self):
    blocks = self.blocks.swapaxes(1, 2).reshape(4096) # Y and Z saved in a inverted order
    blockIDs = np.empty(4096, dtype=np.uint32)
    palette = []
    for i, block in enumerate(blocks):
      # Generate the palette nbt for the given block
      block = nbt.TAG_Compound("", [nbt.TAG_String("name", block.name), nbt.TAG_Short("val", block.dv)])
      if block not in palette:
        palette.append(block)
      blockIDs[i] = palette.index(block)
    return palette, blockIDs

  @classmethod
  def empty(cls, x, z, y):
    subchunk = cls(None, x, z, y)
    subchunk.version = 8
    subchunk.blocks = np.full((16, 16, 16), Block("minecraft:air"), dtype=Block)
    return subchunk

# Generic block storage.
class Block:
  def __init__(self, name, dv=0, nbtData=None):
    self.name = name
    self.dv = dv
    self.nbt = nbtData

  def __repr__(self):
    return "{} {}".format(self.name, self.dv)

# Handles NBT generation for command blocks.
class CommandBlock(Block):
  nameMap = {"I": "command_block", "C": "chain_command_block", "R": "repeating_command_block"}
  dMap = {"d": 0, "u": 1, "-z": 2, "+z": 3, "-x": 4, "+x": 5}
  def __init__(self, cmd="", hover="", block="I", d="u", cond=False, redstone=False):
    name = "minecraft:" + self.nameMap[block]
    dv = self.dMap[d]
    if cond:
      dv += 8
    nbtData = nbt.TAG_Compound("", [])
    nbtData.add(nbt.TAG_String("Command", cmd))
    nbtData.add(nbt.TAG_String("CustomName", hover))
    nbtData.add(nbt.TAG_Byte("TrackOutput", 1))
    nbtData.add(nbt.TAG_Int("Version", 8))
    nbtData.add(nbt.TAG_Byte("auto", int(not redstone)))
    nbtData.add(nbt.TAG_String("id", "CommandBlock"))
    nbtData.add(nbt.TAG_Byte("isMovable", 1))
    nbtData.add(nbt.TAG_Byte("powered", 0))
    nbtData.add(nbt.TAG_Int("LPCommandMode", 0)) # Not sure what these LPModes do. This works.
    nbtData.add(nbt.TAG_Byte("LPConditionalMode", 0))
    nbtData.add(nbt.TAG_Byte("LPRedstoneMode", 0))
    nbtData.add(nbt.TAG_Long("LastExecution", 0))
    nbtData.add(nbt.TAG_String("LastOutput", ""))
    nbtData.add(nbt.TAG_List("LastOutputParams", [nbt.TAG_String(n, str(n)) for n in range(3)]))
    nbtData.add(nbt.TAG_Int("SuccessCount", 0))
    super().__init__(name, dv, nbtData)
