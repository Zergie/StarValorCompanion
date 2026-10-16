"""Persist native window bounds, including negative multi-monitor coordinates."""
import ctypes as C
from ctypes import wintypes as W
import json

user=C.WinDLL('user32',use_last_error=True)
class Placement(C.Structure):
    _fields_=[('length',W.UINT),('flags',W.UINT),('show',W.UINT),('minimum',W.POINT),('maximum',W.POINT),('normal',W.RECT)]
class MonitorInfo(C.Structure):
    _fields_=[('size',W.DWORD),('monitor',W.RECT),('work',W.RECT),('flags',W.DWORD)]
user.GetAncestor.argtypes=[W.HWND,W.UINT];user.GetAncestor.restype=W.HWND
user.GetWindowPlacement.argtypes=[W.HWND,C.POINTER(Placement)]
user.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
user.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
user.MonitorFromRect.argtypes=[C.POINTER(W.RECT),W.DWORD];user.MonitorFromRect.restype=W.HANDLE
user.GetMonitorInfoW.argtypes=[W.HANDLE,C.POINTER(MonitorInfo)]
user.GetWindowLongPtrW.argtypes=[W.HWND,C.c_int];user.GetWindowLongPtrW.restype=C.c_ssize_t
user.AdjustWindowRectEx.argtypes=[C.POINTER(W.RECT),W.DWORD,W.BOOL,W.DWORD]


def load(path):
    try:
        state=json.loads(path.read_text(encoding='utf-8'));b=state['bounds']
        if not isinstance(state.get('maximized',False),bool):return {}
        if not all(isinstance(b[k],int) and not isinstance(b[k],bool) for k in ['x','y','width','height']):return {}
        if not 200<=b['width']<=20000 or not 200<=b['height']<=20000:return {}
        if not -100000<=b['x']<=100000 or not -100000<=b['y']<=100000:return {}
        return state
    except (OSError,ValueError,KeyError,TypeError):return {}


def save(path,state):
    path.parent.mkdir(exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2),encoding='utf-8');tmp.replace(path)


def fit(bounds,work):
    left,top,right,bottom=work
    width=min(max(400,bounds['width']),max(400,right-left-20))
    height=min(max(350,bounds['height']),max(350,bottom-top-50))
    x=max(left,min(bounds['x'],right-width-16));y=max(top,min(bounds['y'],bottom-height-40))
    return dict(x=x,y=y,width=width,height=height)


def window_handle(root):return user.GetAncestor(root.winfo_id(),2)


def capture_handle(hwnd):
    p=Placement();p.length=C.sizeof(p)
    if not user.GetWindowPlacement(hwnd,C.byref(p)):raise OSError('Cannot read window placement.')
    rect=p.normal
    border=W.RECT(0,0,0,0)
    style=user.GetWindowLongPtrW(hwnd,-16)&0xffffffff;extended=user.GetWindowLongPtrW(hwnd,-20)&0xffffffff
    if hasattr(user,'AdjustWindowRectExForDpi'):
        user.GetDpiForWindow.argtypes=[W.HWND];user.GetDpiForWindow.restype=W.UINT
        user.AdjustWindowRectExForDpi.argtypes=[C.POINTER(W.RECT),W.DWORD,W.BOOL,W.DWORD,W.UINT]
        user.AdjustWindowRectExForDpi(C.byref(border),style,False,extended,user.GetDpiForWindow(hwnd))
    else:user.AdjustWindowRectEx(C.byref(border),style,False,extended)
    width=rect.right-rect.left-(border.right-border.left)
    height=rect.bottom-rect.top-(border.bottom-border.top)
    # In normal state screen coordinates avoid WINDOWPLACEMENT's workspace offsets.
    if p.show not in [2,3]:
        screen=W.RECT();user.GetWindowRect(hwnd,C.byref(screen));x,y=screen.left,screen.top
    else:x,y=rect.left,rect.top
    return {'bounds':dict(x=x,y=y,width=width,height=height),'maximized':p.show==3}


def restore(root,state):
    b=state.get('bounds')
    if not b:return
    rect=W.RECT(b['x'],b['y'],b['x']+b['width'],b['y']+b['height'])
    monitor=user.MonitorFromRect(C.byref(rect),2);info=MonitorInfo();info.size=C.sizeof(info)
    if user.GetMonitorInfoW(monitor,C.byref(info)):
        b=fit(b,(info.work.left,info.work.top,info.work.right,info.work.bottom))
    root.geometry(f"{b['width']}x{b['height']}");root.update_idletasks()
    user.SetWindowPos(window_handle(root),None,b['x'],b['y'],0,0,0x0001|0x0004|0x0010)
    if state.get('maximized'):root.state('zoomed')
