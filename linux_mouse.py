from evdev import UInput, ecodes


class LinuxMouseBackend:
    def __init__(self):
        capabilities = {
            ecodes.EV_KEY: [
                ecodes.BTN_LEFT,
                ecodes.BTN_RIGHT,
                ecodes.BTN_MIDDLE,
            ]
        }

        self.device = UInput(
            capabilities,
            name="Gander AutoClicker",
        )

        self.button_codes = {
            "Left": ecodes.BTN_LEFT,
            "Right": ecodes.BTN_RIGHT,
            "Middle": ecodes.BTN_MIDDLE,
        }

    def click(self, button):
        button_code = self.button_codes[button]

        # Button down
        self.device.write(
            ecodes.EV_KEY,
            button_code,
            1,
        )
        self.device.syn()

        # Button up
        self.device.write(
            ecodes.EV_KEY,
            button_code,
            0,
        )
        self.device.syn()

    def close(self):
        self.device.close()
