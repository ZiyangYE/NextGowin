"""Relocate immutable evidence paths and reject original-workspace reads."""
import os,pathlib,sys
root=os.environ.get('NEXTGOWIN_HANDOFF_ROOT')
if root:
    current=root.replace('\\','/').rstrip('/')
    legacy=os.environ.get('NEXTGOWIN_LEGACY_ROOT','').replace('\\','/').rstrip('/')
    original=pathlib.Path.__new__
    def relocated(cls,*args,**kwargs):
        if legacy and args and isinstance(args[0],(str,os.PathLike)):
            text=os.fspath(args[0]).replace('\\','/');lower=text.lower()
            if not (lower==current.lower() or lower.startswith(current.lower()+'/')) and (lower==legacy.lower() or lower.startswith(legacy.lower()+'/')):
                args=(current+text[len(legacy):],*args[1:])
        return original(cls,*args,**kwargs)
    pathlib.Path.__new__=staticmethod(relocated)
    if legacy and os.environ.get('NEXTGOWIN_BLOCK_LEGACY_READS')=='1' and current.lower()!=legacy.lower():
        def guard(event,args):
            if event=='open' and args and isinstance(args[0],(str,bytes)):
                value=os.fsdecode(args[0]);text=os.path.abspath(value).replace('\\','/').lower()
                if text.startswith(legacy.lower()+'/') and not text.startswith(current.lower()+'/'):
                    raise RuntimeError('Reproduction attempted original-workspace access: '+value)
        sys.addaudithook(guard)
