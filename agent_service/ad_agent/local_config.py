"""Local model settings; Windows DPAPI protects secrets for the current OS user."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'var/model.local.json'


def _protect(data: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        raise ValueError('DPAPI secrets require the Windows user that saved them; use environment variables on other hosts')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]
    buf=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte)))
    target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise ValueError('Windows secret protection failed; the secret may belong to a different OS user')
    try:
        return ctypes.string_at(target.data,target.size)
    finally:
        kernel.LocalFree(target.data)


def save_secret(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(_protect(value.encode('utf-8')))


def read_secret(config_path, filename):
    # No arbitrary secret paths in the local JSON, only files in its secrets folder.
    if not isinstance(filename,str) or Path(filename).name!=filename or not filename.endswith('.dpapi'):
        raise ValueError('Invalid local secret filename')
    raw=(Path(config_path).parent/'secrets'/filename).read_bytes()
    if len(raw)>16384:
        raise ValueError('Local secret file is too large')
    return _protect(raw,decrypt=True).decode('utf-8')


def model_config():
    configured=os.getenv('AGENT_MODEL_CONFIG')
    if configured=='':
        return {},DEFAULT_CONFIG
    path=Path(configured) if configured else DEFAULT_CONFIG
    if not path.exists():
        return {},path
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict):
        raise ValueError('Local model configuration must be an object')
    return data,path
