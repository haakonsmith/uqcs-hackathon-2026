import os
import platform
import subprocess
import time
import tempfile


def prompt_and_watch_file(initial_text=""):
  # 1. Create a temporary file and populate it with initial text
  with tempfile.NamedTemporaryFile(
      mode="w+", delete=False, encoding="utf-8"
  ) as tf:
    tf.write(initial_text)
    temp_filename = tf.name

  try:
    current_os = platform.system()
    print(
        f"Detected OS: {current_os}. Triggering native 'Open With' dialog..."
    )

    # 2. Trigger the OS-specific native "Open With" application chooser popup
    if current_os == "Windows":
      subprocess.Popen(
          ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", temp_filename]
      )
    elif current_os == "Darwin":  # macOS
      applescript = (
          f'tell application "Finder" to open (POSIX file "{temp_filename}")'
          " using (choose application)"
      )
      subprocess.Popen(["osascript", "-e", applescript])
    else:  # Linux
      subprocess.Popen(["mimeopen", "-d", temp_filename])

    print("\n[!] Waiting for you to choose an editor, make edits, and save...")
    print("    (Python is automatically watching the file for you...)")

    # 3. Record the initial modification time and size of the file
    initial_mtime = os.path.getmtime(temp_filename)
    
    # Give the user a few seconds to pick their editor so we don't catch the initial state
    time.sleep(3)

    # 4. Automatically loop and wait until the file is modified and saved
    while True:
      try:
        current_mtime = os.path.getmtime(temp_filename)
        
        # Check if the file's modification time has changed
        if current_mtime != initial_mtime:
          # Optional safety check: Try to open the file in write/append mode 
          # to ensure the external editor has finished holding a lock on it.
          with open(temp_filename, "a", encoding="utf-8"):
            pass
            
          print("Changes detected! Editor window closed/saved.")
          break
      except (PermissionError, IOError):
        # PermissionError means the file is still actively locked/open in the editor.
        # We catch it and keep waiting until the editor closes and releases the lock.
        pass

      # Check every second
      time.sleep(1)

    # 5. Read and return the newly updated text automatically
    with open(temp_filename, "r", encoding="utf-8") as tf:
      return tf.read()

  finally:
    # 6. Clean up the temporary file
    if os.path.exists(temp_filename):
      try:
        os.remove(temp_filename)
      except PermissionError:
        pass


if __name__ == "__main__":
  sample_text = "Edit this text in your chosen popup editor, save, and close it."
  
  updated_text = prompt_and_watch_file(sample_text)
  
  print("\n--- Automatically Retrieved Content ---")
  print(updated_text)