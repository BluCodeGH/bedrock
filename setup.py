from setuptools import setup

setup(name="bedrock",
      version="0.1",
      description="A simply python library to access Minecraft: Bedrock Edition worlds.",
      keywords="minecraft bedrock leveldb",
      url="https://github.com/BluCodeGH/bedrock",
      packages=["bedrock"],
      install_requires=["numpy"],
      package_data={
          "bedrock": ["*.dll", "*.so", "LICENCE-LEVELDB"]
      },
      author="BluCode")
