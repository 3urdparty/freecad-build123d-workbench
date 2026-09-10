import os

import FreeCAD as App  # type: ignore[import-not-found]
import FreeCADGui as Gui  # type: ignore[import-not-found]

from .init_gui import ICON_PATH


class _BaseCommand:
    name = "Code_Base"
    text = "Base"
    tooltip = ""
    icon = "code_workbench.svg"

    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICON_PATH, self.icon),
            "MenuText": self.text,
            "ToolTip": self.tooltip or self.text,
        }

    def IsActive(self):
        return True


class StartBridgeCommand(_BaseCommand):
    name = "Code_StartBridge"
    text = "Start Bridge"
    tooltip = "Start the local RPC bridge for external CAD projects"
    icon = "code_workbench.svg"

    def IsActive(self):
        from .bridge.lifecycle import is_running

        return is_running()

    def Activated(self):
        from .bridge.lifecycle import start

        server = start()

        App.Console.PrintMessage(
            f"[Code] bridge listening on 127.0.0.1:{server.port}\n"
        )


class StopBridgeCommand(_BaseCommand):
    name = "Code_StopBridge"
    text = "Stop Bridge"
    tooltip = "Stop the local RPC bridge"
    icon = "code_workbench.svg"

    def IsActive(self):
        from .bridge.lifecycle import is_running

        return not is_running()

    def Activated(self):
        from .bridge.lifecycle import stop

        stop()

        App.Console.PrintMessage(
            "[Code] bridge stopped\n"
        )


class BridgeStatusCommand(_BaseCommand):
    name = "Code_BridgeStatus"
    text = "Bridge Status"
    tooltip = "Show the current bridge connection information"
    icon = "code_workbench.svg"

    def Activated(self):
        from .bridge.lifecycle import get_server

        server = get_server()

        if server is None:
            App.Console.PrintMessage(
                "[Code] bridge is not running\n"
            )
            return

        App.Console.PrintMessage(
            f"[Code] bridge running on 127.0.0.1:{server.port}\n"
        )


ALL_COMMANDS = [
    StartBridgeCommand,
    StopBridgeCommand,
    BridgeStatusCommand,
]


def register_all() -> list[str]:
    names = []

    for cls in ALL_COMMANDS:
        Gui.addCommand(cls.name, cls())
        names.append(cls.name)

    return names
