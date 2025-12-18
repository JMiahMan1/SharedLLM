import pychromecast

print("\n--- Testing Chromecast instantiation ---")

TARGET_IP = "192.168.2.240"

try:
    # Try the standard way
    cast = pychromecast.Chromecast(TARGET_IP)
    print(f"Type of cast object: {type(cast)}")
    print(f"\nAttributes of cast object:")
    attrs = [attr for attr in dir(cast) if not attr.startswith('_')]
    for attr in attrs:
        print(f"  - {attr}")
    
    print(f"\ncast object repr: {repr(cast)}")
    
    # Try to wait
    cast.wait()
    print("wait() succeeded")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
