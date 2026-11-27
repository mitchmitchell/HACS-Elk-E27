# https://github.com/mitchmitchell/elke27/blob/5f247f995883ee5a12d5db8ce15472325bee9db1/devtools/privacc_probes.py
# vendored from elke27 5f247f9 (#176); do not local-edit
"""Shared probe set for ``privacc.py``: every private-reach form review has caught.

Each :class:`Probe` is a one-statement source snippet and the forms
:func:`privacc.reach` must report for it (an empty tuple means the snippet is public
and must count zero). The set covers the elke27 #162 review (B3), the counter gaps
from the #162 D55 review, and the HACS #194 review (B1-B3). Run with
:data:`PACKAGES` watched; a vendored copy of ``privacc.py`` must pass every probe
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

PACKAGES: tuple[str, ...] = ("elke27_lib", "custom_components", "homeassistant")

_IMPORTS = (
    "from unittest import mock\n"
    "import unittest.mock\n"
    "from unittest.mock import patch\n"
    "from elke27_lib.client import Elke27Client\n"
    "from homeassistant.core import HomeAssistant\n"
)


@dataclass(frozen=True)
class Probe:
    """A snippet (appended to a shared import header) and the forms it must yield."""

    source: str
    forms: tuple[str, ...]

    def module(self) -> str:
        """The full module text: the shared imports, then the snippet."""
        return _IMPORTS + self.source


PROBES: tuple[Probe, ...] = (
    Probe("patch.object(hub, '_client')\n", ("patch.object(..., '_client')",)),
    Probe("mock.patch.object(hub, '_client')\n", ("patch.object(..., '_client')",)),
    Probe("mocker.patch.object(hub, '_client')\n", ("patch.object(..., '_client')",)),
    Probe("unittest.mock.patch.object(hub, '_client')\n", ("patch.object(..., '_client')",)),
    Probe("patch.object(hub, attribute='_client')\n", ("patch.object(..., '_client')",)),
    Probe(
        "mock.patch.multiple(hub, _g=1, public=2)\n",
        ("patch.multiple(..., _g=)",),
    ),
    Probe("mocker.patch.multiple(hub, _h=1)\n", ("patch.multiple(..., _h=)",)),
    Probe(
        "patch('elke27_lib.client._helper')\n",
        ("patch('elke27_lib.client._helper')",),
    ),
    Probe(
        "patch(target='homeassistant.exceptions._function_cache')\n",
        ("patch('homeassistant.exceptions._function_cache')",),
    ),
    Probe(
        "patch.dict('homeassistant.exceptions._function_cache', {})\n",
        ("patch.dict('homeassistant.exceptions._function_cache')",),
    ),
    Probe(
        "monkeypatch.setattr('elke27_lib.client._helper', None)\n",
        ("setattr('elke27_lib.client._helper')",),
    ),
    Probe("monkeypatch.setattr(hub, '_client', None)\n", ("setattr(..., '_client')",)),
    Probe(
        "monkeypatch.setattr(hub, name='_client', value=None)\n",
        ("setattr(..., '_client')",),
    ),
    Probe("monkeypatch.delattr(hub, '_client')\n", ("delattr(..., '_client')",)),
    Probe("setattr(hub, '_client', None)\n", ("setattr(..., '_client')",)),
    Probe("getattr(hub, '_client')\n", ("getattr(..., '_client')",)),
    Probe("hasattr(hub, '_client')\n", ("hasattr(..., '_client')",)),
    Probe("object.__setattr__(hub, '_client', None)\n", ("__setattr__(..., '_client')",)),
    Probe("hub.__setattr__('_client', None)\n", ("__setattr__(..., '_client')",)),
    Probe("object.__getattribute__(hub, '_client')\n", ("__getattribute__(..., '_client')",)),
    Probe("hub.__dict__['_client'] = None\n", ("['_client'] via __dict__/vars()",)),
    Probe("vars(hub)['_client'] = None\n", ("['_client'] via __dict__/vars()",)),
    Probe(
        "from homeassistant.exceptions import _function_cache\n",
        ("from homeassistant.exceptions import _function_cache",),
    ),
    Probe(
        "from elke27_lib._session import Session\n",
        ("from elke27_lib._session import",),
    ),
    Probe("import elke27_lib._session\n", ("import elke27_lib._session",)),
    Probe(
        "from custom_components.elke27.hub import _build\n",
        ("from custom_components.elke27.hub import _build",),
    ),
    Probe(
        "class Sub(Elke27Client):\n    def _build_area_map(self):\n        return {}\n",
        ("Sub._build_area_map override",),
    ),
    Probe(
        "class Sub(Elke27Client):\n    async def _connect(self):\n        return None\n",
        ("Sub._connect override",),
    ),
    Probe("class Sub(Elke27Client):\n    _client = None\n", ("Sub._client override",)),
    Probe("class Sub(HomeAssistant):\n    _state: int = 0\n", ("Sub._state override",)),
    Probe(
        "mocker.patch.object(target=hub, attribute='_client')\n",
        ("patch.object(..., '_client')",),
    ),
    Probe(
        "monkeypatch.setattr(target='elke27_lib.client._x', value=1)\n",
        ("setattr('elke27_lib.client._x')",),
    ),
    Probe("hub.__dict__.get('_client')\n", ("__dict__/vars().get('_client')",)),
    Probe("vars(hub).get('_client')\n", ("__dict__/vars().get('_client')",)),
    Probe("hub.__dict__.pop('_client', None)\n", ("__dict__/vars().pop('_client')",)),
    Probe("hub.__dict__.update(_client=1)\n", ("__dict__/vars().update('_client')",)),
    Probe("vars(hub).update({'_client': 1})\n", ("__dict__/vars().update('_client')",)),
    Probe("patch.dict(hub.__dict__, {'_client': 1})\n", ("patch.dict(..., '_client')",)),
    Probe("mock.patch.dict(hub.__dict__, _client=1)\n", ("patch.dict(..., '_client')",)),
    Probe("operator.attrgetter('_client')\n", ("attrgetter('_client')",)),
    Probe("attrgetter('kernel._session')\n", ("attrgetter('kernel._session')",)),
    Probe("operator.methodcaller('_connect')\n", ("methodcaller('_connect')",)),
    Probe(
        "Base = Elke27Client\nclass Sub(Base):\n    def _m(self):\n        return 1\n",
        ("Sub._m override",),
    ),
    Probe(
        "patch.dict(in_dict='elke27_lib.client._cache', values={})\n",
        ("patch.dict('elke27_lib.client._cache')",),
    ),
    Probe("hub.__dict__.setdefault('_client', 1)\n", ("__dict__/vars().setdefault('_client')",)),
    Probe("object.__getattr__(hub, '_client')\n", ("__getattr__(..., '_client')",)),
    Probe(
        "class A(Elke27Client):\n    pass\nclass B(A):\n    def _m(self):\n        return 1\n",
        ("B._m override",),
    ),
    Probe(
        "A = B = Elke27Client\nclass Sub(B):\n    def _m(self):\n        return 1\n",
        ("Sub._m override",),
    ),
    Probe(
        "Base: type = Elke27Client\nclass Sub(Base):\n    def _m(self):\n        return 1\n",
        ("Sub._m override",),
    ),
    Probe("patch.dict(os.environ, {'_X': '1'})\n", ()),
    Probe("patch.dict(sys.modules, {'_foo': 1})\n", ()),
    Probe("from .elke27_lib._mod import X\n", ()),
    Probe("setattr(*args)\n", ()),
    Probe("patch.object(*args)\n", ()),
    Probe("patch.object(*args, '_client')\n", ()),
    Probe("hub.__dict__.get('public')\n", ()),
    Probe("operator.attrgetter('public.name')\n", ()),
    Probe("mock.patch.object(hub, 'public')\n", ()),
    Probe("patch('elke27_lib.client.Elke27Client')\n", ()),
    Probe("vars(hub)['public'] = 1\n", ()),
    Probe("getattr(hub, '__class__')\n", ()),
    Probe("from json import _default_decoder\n", ()),
    Probe("class Local:\n    _client = None\n    def _x(self):\n        return 1\n", ()),
    Probe("patch.dict('_env', {})\n", ()),
    Probe(
        "patch.multiple('elke27_lib.client._Helper', x=1)\n",
        ("patch.multiple('elke27_lib.client._Helper')",),
    ),
    Probe(
        "patch.dict(in_dict=hub.__dict__, values={'_client': 1})\n",
        ("patch.dict(..., '_client')",),
    ),
    Probe("settings['_token'] = 1\n", ()),
    Probe("import _thread\n", ()),
    Probe(
        "try:\n    Base = Elke27Client\nexcept ImportError:\n    Base = object\n"
        "class Mid(Base):\n    pass\nclass Sub(Mid):\n    def _m(self):\n        return 1\n",
        ("Sub._m override",),
    ),
    Probe(
        "from .elke27_lib.client import Elke27Client as Local\n"
        "class Sub(Local):\n    def _m(self):\n        return 1\n",
        (),
    ),
)
