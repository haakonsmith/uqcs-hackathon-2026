import sys

def beep():
    if sys.platform == "win32":
        import winsound
        winsound.Beep(1000, 150)  # 1000 Hz for 200ms
        winsound.Beep(1000, 150) 
        winsound.Beep(1000, 150) 
    else:
        # Standard terminal bell works reliably on macOS and Linux terminals
        print("\a", end="", flush=True)
