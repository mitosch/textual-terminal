"""
An example how to use the Terminal widget with bash and htop
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Placeholder
from textual.containers import Vertical
from textual.css.query import NoMatches

from textual import log

from textual_terminal import Terminal


class TerminalPlaceholder(Placeholder):
    pass


class TerminalExampleApp(App):
    BINDINGS = [
        ("Q", "quit", "Exit"),
        ("1", "start_1", "Start Terminal 1"),
        ("2", "start_2", "Start Terminal 2"),
    ]

    DEFAULT_CSS = """
    .terminals {
        height: 1fr;
    }
    .terminals TerminalPlaceholder {
        width: 80;
        height: 24;
        margin-bottom: 1;
    }
    .terminals #terminal_1 {
        width: 80;
        height: 24;
        margin-bottom: 1;
    }
    .terminals #terminal_2 {
        width: 80;
        height: 24;
    }
    """

    COMMANDS = {
        "terminal_1": "htop -d10",
        "terminal_2": "bash",
    }

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

        yield Vertical(
            Static("Terminal 1:", id="terminal_1_label"),
            TerminalPlaceholder("Terminal 1", id="terminal_1_ph"),
            # Terminal(self.COMMANDS["terminal_1"]),
            Static("Terminal 2:", id="terminal_2_label"),
            TerminalPlaceholder("Terminal 2", id="terminal_2_ph"),
            # Terminal(self.COMMANDS["terminal_2"]),
            classes="terminals",
        )

    def attach_terminal(self, name: str) -> None:
        try:
            self.query_one(f"#{name}_ph").remove()
        except NoMatches:
            log.warning(f"terminal {name} already attached")
            return

        log("attach terminal with id:", name)
        self.app.mount(
            Terminal(command=self.COMMANDS[name], id=name), after=f"#{name}_label"
        )

    def start_terminal(self, name: str) -> None:
        try:
            terminal: Terminal = self.app.query_one(f"Terminal#{name}")
        except NoMatches:
            log("no matches:", f"Terminal#{name}")
            return

        terminal.start()

    def stop_terminal(self, name: str) -> None:
        try:
            terminal: Terminal = self.app.query_one(f"Terminal#{name}")
        except NoMatches:
            log("no matches:", f"Terminal#{name}")
            return

        terminal.stop()

    def action_start_1(self) -> None:
        self.attach_terminal("terminal_1")
        self.start_terminal("terminal_1")

    def action_start_2(self) -> None:
        self.attach_terminal("terminal_2")
        self.start_terminal("terminal_2")


if __name__ == "__main__":
    app = TerminalExampleApp()
    app.run()
