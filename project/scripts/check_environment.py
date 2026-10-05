import platform, sys, json
info={"python":sys.version.split()[0],"platform":platform.platform(),"machine":platform.machine()}
mods=["flask","torch","transformers","accelerate","peft","sklearn","pandas","rapidfuzz"]
for name in mods:
    try:
        m=__import__(name)
        info[name]=getattr(m,"__version__","installed")
    except Exception as e:
        info[name]=f"ERROR: {e!r}"
try:
    import torch
    info["cuda_available"]=torch.cuda.is_available()
    info["mps_available"]=bool(getattr(torch.backends,"mps",None) and torch.backends.mps.is_available())
except Exception:
    pass
print(json.dumps(info,ensure_ascii=False,indent=2))
