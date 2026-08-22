import time

import blessed

# --- Solid Block Title Art ("TERMINATION") ---
TITLE_ART = [
    " ██████ ███████ ██████  ██      ██ ██ ███    ██  █████  ████████ ██   ██████  ██    ██",
    "   ██   ██      ██   ██ ████  ████ ██ ████   ██ ██   ██    ██    ██  ██    ██ ███   ██",
    "   ██   █████   ██████  ██ ████ ██ ██ ██ ██  ██ ███████    ██    ██  ██    ██ ██ ██ ██",
    "   ██   ██      ██   ██ ██  ██  ██ ██ ██  ██ ██ ██   ██    ██    ██  ██    ██ ██  ████",
    "   ██   ███████ ██   ██ ██      ██ ██ ██   ████ ██   ██    ██    ██   ██████  ██   ███",
]

# --- World Map Template ---
WORLD_MAP = [
    "           ################      ##`             #                ",
    "         ######## #########      ##               #               ",
    "          ######    #######                   ######    # #       ",
    "        ##########  ########             ##################       ",
    " ##################  ####         ##############################  ",
    " ##################   #    #    ###############################   ",
    "  ##################             ########################         ",
    "        ############        #############################  #      ",
    "        #############         ##########################          ",
    "         ########            ############################         ",
    "          ######            #########################             ",
    " #          ###  ##        ##############  ### ####               ",
    "                #####       ###########                           ",
    "                ########         ######          # #   #          ",
    "                 #######         #######              ###         ",
    "                 ######          #### ##           ########       ",
    "                 ####             ##                #######       ",
    "                  #                                      #        ",
    "                 ##                                               ",
    "                                                                  ",
]

MENU_ITEMS = [{"name": "JOIN GAME", "pos": "center_top"}, {"name": "EXIT", "pos": "center_bottom"}, {"name": "SETTINGS", "pos": "top_right"}]


def get_button_geometry(item, h, w):
    name = item["name"]
    btn_len = len(name) + 4
    if item["pos"] == "top_right":
        bx = max(0, w - btn_len - 4)
        by = 7
    elif item["pos"] == "center_top":
        bx = max(0, (w - btn_len) // 2)
        by = h // 2 + 2
    elif item["pos"] == "center_bottom":
        bx = max(0, (w - btn_len) // 2)
        by = h // 2 + 4
    else:
        bx, by = 0, 0
    return bx, by, btn_len


def render_static_background(term: blessed.Terminal):
    """Renders the ocean, world map, and title ONCE so they never rerender unnecessarily."""
    h = term.height
    w = term.width
    output = [term.home]

    # 1. Fill background with ocean blocks & world map landmasses
    map_h = len(WORLD_MAP)
    map_w = len(WORLD_MAP[0]) * 2
    y_offset = 2
    start_y = max(0, (h - map_h) // 2) + y_offset  # renders everything in the middle
    start_x = max(0, (w - map_w) // 2)

    map_rows = {start_y + r_idx: row for r_idx, row in enumerate(WORLD_MAP)}

    for y in range(h):
        row_str = []
        has_map = y in map_rows
        map_row_text = map_rows.get(y, "")

        x = 0
        while x < w - 1:
            is_land = False
            if has_map:
                rel_x = x - start_x
                if rel_x >= 0 and rel_x % 2 == 0:
                    c_idx = rel_x // 2
                    if c_idx < len(map_row_text) and map_row_text[c_idx] != " ":
                        is_land = True

            if is_land:
                row_str.append(term.green_on_blue("██"))
            else:
                row_str.append(term.blue_on_blue("██"))
            x += 2

        output.append(term.move_xy(0, y) + "".join(row_str))

    # 2. Render Title Art with Transparent Gaps
    for i, line in enumerate(TITLE_ART):
        tx = max(0, (w - len(line)) // 2)
        title_start = 1  # Height the title is rendered at from the top
        ty = title_start + i
        if ty < h:
            for c_idx, char in enumerate(line):
                x = tx + c_idx
                if 0 <= x < w:
                    if char != " ":
                        # Draw the letter blocks
                        output.append(term.move_xy(x, ty) + term.red_on_blue(char))
                    else:
                        # Draw solid blue over the gaps to hide the map
                        output.append(term.move_xy(x, ty) + term.blue_on_blue(" "))

    print("".join(output), end="", flush=True)


def render_menu_buttons(term: blessed.Terminal, selected_idx: int):
    """Updates the menu buttons with high-contrast, chunky button styling."""
    h = term.height
    w = term.width
    output = []

    for idx, item in enumerate(MENU_ITEMS):
        name = item["name"]
        is_selected = idx == selected_idx

        # Give unselected and selected buttons distinct visual borders
        if is_selected:
            btn_str = f"[>{name}<]"
        else:
            btn_str = f"[ {name} ]"

        bx, by, _ = get_button_geometry(item, h, w)

        if by < h:
            if is_selected:
                # SELECTED BUTTON: High visibility (e.g., Bold Black text on Bright Yellow background)
                output.append(term.move_xy(bx, by) + term.bold_black_on_white(btn_str))
            else:
                # UNSELECTED BUTTON: Clean retro style (e.g., Bold Cyan text on Blue background)
                output.append(term.move_xy(bx, by) + term.bold_cyan_on_blue(btn_str))

    print("".join(output), end="", flush=True)


# Attatched functionality for buttons here
def handle_action(term, action):
    if action == "EXIT":
        return True
    elif action == "SETTINGS" or action == "JOIN GAME":
        # 1. Clear the entire screen to black
        term.clear()

        # 2. Show only the centered message
        msg = f"Opening: {action}..."
        mx = max(0, (term.width - len(msg)) // 2)
        my = term.height // 2
        print(term.move_xy(mx, my) + term.black_on_white(msg), end="", flush=True)

        # 3. Pause for a second, then trigger the settings configuration
        time.sleep(1)
        return False


def main():
    term = blessed.Terminal()
    selected_idx = 0

    with term.cbreak(), term.hidden_cursor(), term.fullscreen(), term.mouse_enabled():
        size = (term.width, term.height)

        # Draw the background and title once at startup
        render_static_background(term)
        render_menu_buttons(term, selected_idx)

        while True:
            key = term.inkey(timeout=0.2)

            # Handle terminal window resizing (only time map/title needs to redraw)
            if (term.width, term.height) != size:
                size = (term.width, term.height)
                render_static_background(term)
                render_menu_buttons(term, selected_idx)
                continue

            if not key:
                continue

            # Handle Keyboard Navigation (Only redraws buttons!)
            if key.name == "KEY_UP":
                selected_idx = (selected_idx - 1) % len(MENU_ITEMS)
                render_menu_buttons(term, selected_idx)
            elif key.name == "KEY_DOWN":
                selected_idx = (selected_idx + 1) % len(MENU_ITEMS)
                render_menu_buttons(term, selected_idx)
            elif key.name in ("KEY_ENTER", "KEY_RETURN", "\n", "\r"):
                action = MENU_ITEMS[selected_idx]["name"]
                if handle_action(term, action):
                    break
                # Re-render background/buttons after returning from action overlay
                render_static_background(term)
                render_menu_buttons(term, selected_idx)
            elif key.lower() == "q":
                break

            # Handle Mouse Click Events
            elif key.name == "MOUSE_LEFT":
                my, mx = key.mouse_yx
                for idx, item in enumerate(MENU_ITEMS):
                    bx, by, btn_len = get_button_geometry(item, term.height, term.width)
                    if by == my and bx <= mx < bx + btn_len:
                        if selected_idx != idx:
                            selected_idx = idx
                            render_menu_buttons(term, selected_idx)
                        else:
                            if handle_action(term, item["name"]):
                                return
                            render_static_background(term)
                            render_menu_buttons(term, selected_idx)
                        break


if __name__ == "__main__":
    main()
