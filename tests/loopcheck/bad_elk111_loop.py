import importlib
from importlib.metadata import version


def f() -> object:
    return importlib.import_module("json")


def g() -> str:
    return version("elke27")


def h() -> object:
    return __import__("json")
