# A simple and flexible NBT parser.

import struct

# Allows for easy sequential reading of binary data
class DataReader:
  def __init__(self, data):
    self.data = data
    self.idx = 0

  def pop(self, size):
    fmt = [None, "b", "h", None, "i", None, None, None, "q"]
    popped = struct.unpack("<{}".format(fmt[size]), self.data[self.idx:self.idx + size])[0]
    self.idx += size
    return popped

  # Specific to the NBT string format, two bytes for size followed by that many bytes of string.
  def popString(self):
    size = self.pop(2)
    popped = struct.unpack("<{}s".format(size), self.data[self.idx:self.idx + size])[0]
    self.idx += size
    return popped.decode("utf-8")

  # Useful when dealing with an unknown number of compound tags back to back.
  def finished(self):
    return self.idx >= len(self.data)

  def __repr__(self):
    return self.data[self.idx:]

# Allows for easy sequential writing of binary data.
class DataWriter:
  def __init__(self):
    self.data = b""

  def put(self, size, *data):
    fmt = [None, "b", "h", None, "i", None, None, None, "q"]
    self.data += struct.pack("<{}".format(fmt[size]), *data)

  def putString(self, string):
    self.put(2, len(string))
    self.data += struct.pack("<{}s".format(len(string)), string.encode("utf-8"))

  def __repr__(self):
    return self.data

tags = [None for _ in range(13)]

# Generic base tag, calls self.decode with binary data to fill in payload.
class TAG:
  ID = None
  def __init__(self, name, data):
    self.name = name
    if isinstance(data, DataReader):
      self.payload = self.decode(data)
    else:
      self.payload = data

  def decode(self, data):
    raise NotImplementedError("Decode method not overridden by subclass.")

  def __getitem__(self, name):
    for item in self.payload:
      if item.name == name:
        return item
    raise KeyError("{} not found in {}".format(name, self.payload))

  def __eq__(self, other):
    return self.name == other.name and self.payload == other.payload and self.ID == other.ID

  def __repr__(self):
    return "{}-{}:{}".format(self.__class__.__name__, self.name, self.payload)

class TAG_Byte(TAG):
  ID = 1
  def decode(self, data):
    return data.pop(1)

  def encode(self, data):
    data.put(1, self.payload)
tags[1] = TAG_Byte

class TAG_Short(TAG):
  ID = 2
  def decode(self, data):
    return data.pop(2)

  def encode(self, data):
    data.put(2, self.payload)
tags[2] = TAG_Short

class TAG_Int(TAG):
  ID = 3
  def decode(self, data):
    return data.pop(4)

  def encode(self, data):
    data.put(4, self.payload)
tags[3] = TAG_Int

class TAG_Long(TAG):
  ID = 4
  def decode(self, data):
    return data.pop(8)
  def encode(self, data):
    data.put(8, self.payload)
tags[4] = TAG_Long

class TAG_String(TAG):
  ID = 8
  def decode(self, data):
    return data.popString()
  def encode(self, data):
    data.putString(self.payload)
tags[8] = TAG_String

# Similar to TAG_List, except the type of tag is not specified, as we know it is a byte.
class TAG_Byte_Array(TAG):
  ID = 7
  def decode(self, data):
    size = data.pop(4)
    payload = []
    for i in range(size):
      payload.append(TAG_Byte(i, data))
    return payload

  def encode(self, data):
    data.put(4, len(self.payload)) # Size
    for item in self.payload:
      item.encode(data)
tags[7] = TAG_Byte_Array

# Basically a TAG_Compound, but the items don't have names, and instead are named integer indexes.
#  This allows for a generic __getitem__ function in the TAG class.
class TAG_List(TAG):
  ID = 9
  def decode(self, data):
    self.itemID = data.pop(1)
    size = data.pop(4)
    payload = []
    for i in range(size):
      payload.append(tags[self.itemID](i, data))
    return payload

  def encode(self, data):
    if self.payload == []: # We don't know the data type.
      data.put(1, 0) # Default to TAG_End
    else:
      data.put(1, tags.index(type(self.payload[0])))
    data.put(4, len(self.payload))
    for item in self.payload:
      item.encode(data)

  def add(self, tag):
    self.payload.append(tag)
tags[9] = TAG_List

# Stores some number of complete tags, followed by a TAG_End
class TAG_Compound(TAG):
  ID = 10
  def decode(self, data):
    payload = []
    tagID = data.pop(1)
    while tagID != 0:
      if tags[tagID] is not None:
        name = data.popString()
        payload.append(tags[tagID](name, data))
      else:
        raise NotImplementedError("Tag {} not implemented.".format(tagID))
      tagID = data.pop(1)
    return payload

  def encode(self, data):
    for item in self.payload:
      data.put(1, item.ID)
      data.putString(item.name)
      item.encode(data)
    data.put(1, 0)

  def add(self, tag):
    self.payload.append(tag)

  def pop(self, name):
    for i in range(len(self.payload)):
      if self.payload[i].name == name:
        return self.payload.pop(i)
    return None
tags[10] = TAG_Compound

def decode(data):
  tagID = data.pop(1)
  if tags[tagID] is not None:
    name = data.popString()
    return tags[tagID](name, data)
  raise NotImplementedError("Tag {} not implemented.".format(tagID))

def encode(toEncode, data=None):
  data = data or DataWriter()
  data.put(1, toEncode.ID)
  data.putString(toEncode.name)
  toEncode.encode(data)
  return data.data
