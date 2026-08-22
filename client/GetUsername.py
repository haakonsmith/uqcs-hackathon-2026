import blessed


def get_username(term: blessed.Terminal) -> str:
    """Prompts the user to enter their username in a clean terminal popup."""
    input_buffer = ""
    
    with term.cbreak(), term.hidden_cursor(), term.fullscreen():
        while True:
            term.clear()
            h, w = term.height, term.width
            
            title = "=== ENTER YOUR USERNAME ==="
            prompt = f"Name: {input_buffer}_"
            hint = "[Enter] to Confirm | [ESC] to Cancel"

            start_y = h // 2 - 2
            
            output = [
                term.home,
                term.move_xy(max(0, (w - len(title)) // 2), start_y) + term.bold_yellow(title),
                term.move_xy(max(0, (w - len(prompt)) // 2), start_y + 2) + term.cyan(prompt),
                term.move_xy(max(0, (w - len(hint)) // 2), start_y + 4) + term.dim_white(hint)
            ]
            print("".join(output), end="", flush=True)

            key = term.inkey(timeout=0.2)
            if not key:
                continue

            if key.name == "KEY_ESCAPE":
                return "Player"  # Default fallback name if cancelled
            elif key.name in ("KEY_ENTER", "KEY_RETURN", "\n", "\r"):
                if input_buffer.strip():
                    return input_buffer.strip()
                return "Player"
            elif key.name == "KEY_BACKSPACE" or key.code == term.KEY_BACKSPACE:
                input_buffer = input_buffer[:-1]
            elif key.is_sequence:
                continue
            else:
                input_buffer += key