"""
A terminal emulator for Textual.

Based on David Brochart's pyte example:
https://github.com/selectel/pyte/blob/master/examples/terminal_emulator.py
"""

from __future__ import annotations

# FIXME: when hitting Alt+e, app is waiting for any stdin (output not shown)
# TODO: do not show cursor when widget is not focused

import os
import fcntl
import signal
import shlex
import asyncio
from asyncio import Task
import pty
import struct
import termios
import re
from pathlib import Path

import pyte
from pyte.screens import Char

from rich.text import Text
from rich.style import Style
from rich.color import ColorParseError

from textual.widget import Widget
from textual import events

from textual import log


class TerminalPyteScreen(pyte.Screen):
    """Overrides the pyte.Screen class to be used with TERM=linux."""

    def set_margins(self, *args, **kwargs):
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)


class TerminalDisplay:
    """Rich display for the terminal."""

    def __init__(self, lines):
        self.lines = lines

    def __rich_console__(self, _console, _options):
        line: Text
        for line in self.lines:
            yield line


_re_ansi_sequence = re.compile(r'(\x1b\[\??[\d;]*[a-zA-Z])')
DECSET_PREFIX = "\x1b[?"


class Terminal(Widget, can_focus=True):
    """Terminal textual widget."""

    DEFAULT_CSS = """
    Terminal {
        background: $background;
    }
    """

    def __init__(
        self,
        command: str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.command = command

        # default size, will be adapted on_resize
        self.ncol = 80
        self.nrow = 24
        self.mouse_tracking = False

        # variables used when starting the emulator: self.start()
        self.emulator: TerminalEmulator = None
        self.send_queue: asyncio.Queue = None
        self.recv_queue: asyncio.Queue = None
        self.recv_task: Task = None

        # OPTIMIZE: check a way to use textual.keys
        self.ctrl_keys = {
            "up": "\x1bOA",
            "down": "\x1bOB",
            "right": "\x1bOC",
            "left": "\x1bOD",
            "home": "\x1bOH",
            "end": "\x1b[F",
            "delete": "\x1b[3~",
            "pageup": "\x1b[5~",
            "pagedown": "\x1b[6~",
            "shift+tab": "\x1b[Z",
            "f1": "\x1bOP",
            "f2": "\x1bOQ",
            "f3": "\x1bOR",
            "f4": "\x1bOS",
            "f5": "\x1b[15~",
            "f6": "\x1b[17~",
            "f7": "\x1b[18~",
            "f8": "\x1b[19~",
            "f9": "\x1b[20~",
            "f10": "\x1b[21~",
            "f11": "\x1b[23~",
            "f12": "\x1b[24~",
            "f13": "\x1b[25~",
            "f14": "\x1b[26~",
            "f15": "\x1b[28~",
            "f16": "\x1b[29~",
            "f17": "\x1b[31~",
            "f18": "\x1b[32~",
            "f19": "\x1b[33~",
            "f20": "\x1b[34~",
        }
        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self.stream = pyte.Stream(self._screen)

        super().__init__(name=name, id=id, classes=classes)

    def start(self) -> None:
        if self.emulator is not None:
            return

        self.emulator = TerminalEmulator(command=self.command)
        self.emulator.start()
        self.send_queue = self.emulator.recv_queue
        self.recv_queue = self.emulator.send_queue
        self.recv_task = asyncio.create_task(self.recv())

    def stop(self) -> None:
        if self.emulator is None:
            return

        self._display = self.initial_display()

        self.recv_task.cancel()

        self.emulator.stop()
        self.emulator = None

    def render(self):
        return self._display

    async def on_key(self, event: events.Key) -> None:
        if self.emulator is None:
            return

        if event.key == "ctrl+f1":
            # release focus from widget: because event.stop() follows, releasing
            # focus would not be possible without mouse click.
            #
            # OPTIMIZE: make the key to release focus configurable
            self.app.set_focus(None)
            return

        event.stop()
        char = self.ctrl_keys.get(event.key) or event.character
        if char:
            await self.send_queue.put(["stdin", char])

    async def on_resize(self, _event: events.Resize) -> None:
        if self.emulator is None:
            return

        self.ncol = self.size.width
        self.nrow = self.size.height
        await self.send_queue.put(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)

    async def on_click(self, event: events.MouseEvent):
        if self.emulator is None:
            return

        if self.mouse_tracking is False:
            return

        await self.send_queue.put(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown):
        if self.emulator is None:
            return

        if self.mouse_tracking is False:
            return

        await self.send_queue.put(["scroll", "down", event.x, event.y])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp):
        if self.emulator is None:
            return

        if self.mouse_tracking is False:
            return

        await self.send_queue.put(["scroll", "up", event.x, event.y])

    async def recv(self):
        try:
            while True:
                message = await self.recv_queue.get()
                cmd = message[0]
                if cmd == "setup":
                    await self.send_queue.put(["set_size", self.nrow, self.ncol])
                elif cmd == "stdout":
                    chars = message[1]

                    # log("recv stdout:", chars)

                    for sep_match in re.finditer(_re_ansi_sequence, chars):
                        sequence = sep_match.group(0)
                        if sequence.startswith(DECSET_PREFIX):
                            parameters = sequence.removeprefix(DECSET_PREFIX).split(";")
                            if "1000h" in parameters:
                                self.mouse_tracking = True
                            if "1000l" in parameters:
                                self.mouse_tracking = False

                    try:
                        self.stream.feed(chars)
                    except TypeError as error:
                        # pyte could get into errors here: Screen.cursor_position()
                        # is getting 4 args. Happens when TERM=linux and using
                        # w3m (default options).

                        # This also happened when TERM is not set to "linux" and w3m
                        # is started without the option "-no-mouse".
                        log.warning("could not feed:", error)

                    lines = []
                    for y in range(self._screen.lines):
                        line_text = Text()
                        line = self._screen.buffer[y]
                        for x in range(self._screen.columns):
                            char: Char = line[x]

                            style = self.define_style(char)
                            line_text.append(char.data, style=style)

                            if (
                                self._screen.cursor.x == x and
                                self._screen.cursor.y == y
                            ):
                                line_text.stylize("reverse", x, x + 1)

                        lines.append(line_text)

                    self._display = TerminalDisplay(lines)
                    self.refresh()

                elif cmd == "disconnect":
                    self.stop()
        except asyncio.CancelledError:
            # log.warning("Terminal.recv cancelled")
            pass

    def define_style(self, char: Char) -> Style | None:
        # OPTIMIZE: refactor from rich-style per character to string changing style
        if char.fg == "default" and char.bg == "default":
            return None

        style: Style
        try:
            style = Style(
                color=self.fix_color(char.fg),
                bgcolor=self.fix_color(char.bg),
                bold=char.bold,
            )
        except ColorParseError as error:
            # TODO: fish is using hex-colors without # / and elinks in 256-colors
            #   RE: ([0-9a-f]{6})
            style = None
            log.warning("color parse error:", error)

        return style

    def fix_color(self, color: str) -> str:
        """Fix wrong ANSI color names.

        Examples:
          * htop is using "brown" => not an ANSI color
        """
        # OPTIMIZE: find a way to catch these errors and use a default color.

        if color == "brown":
            return "yellow"

        return color

    def initial_display(self) -> TerminalDisplay:
        """Returns the display when initially creating the terminal or clearing it."""

        return TerminalDisplay([Text()])


class TerminalEmulator:
    def __init__(self, command: str):
        # FIXME: fix ResourceWarning (manually close the fd / p_out broke (blocking)
        """
        The following error happens on self.fd, when stopping the emulator with stop():

        ResourceWarning: unclosed file <_io.FileIO name=8 mode='rb+' closefd=True>

        With the try-except blocks around the while True, the warnings are now
        appearing immediately. But closing fd or p_out there, still causes a
        crash/block/hang or the warning is still there...

        It maybe has to be implemented somewhere at the CancelledError.
        """
        self.ncol = 80
        self.nrow = 24
        self.data_or_disconnect = None
        self.run_task: asyncio.Task = None
        self.send_task: asyncio.Task = None

        self.fd = self.open_terminal(command=command)
        self.p_out = os.fdopen(self.fd, "w+b", 0)  # 0: buffering off
        self.recv_queue = asyncio.Queue()
        self.send_queue = asyncio.Queue()
        self.event = asyncio.Event()

    def start(self):
        self.run_task = asyncio.create_task(self._run())
        self.send_task = asyncio.create_task(self._send_data())

    def stop(self):
        self.run_task.cancel()
        self.send_task.cancel()

        os.kill(self.pid, signal.SIGTERM)
        os.waitpid(self.pid, 0)

    def open_terminal(self, command: str):
        self.pid, fd = pty.fork()
        if self.pid == 0:
            argv = shlex.split(command)
            # OPTIMIZE: do not use a fixed LC_ALL
            env = dict(TERM="xterm", LC_ALL="en_US.UTF-8", HOME=str(Path.home()))
            os.execvpe(argv[0], argv, env)

        return fd

    async def _run(self):
        loop = asyncio.get_running_loop()

        def on_output():
            try:
                self.data_or_disconnect = self.p_out.read(65536).decode()
                self.event.set()
            except UnicodeDecodeError as error:
                # NOTE: this happens sometimes, eg in w3m browsing wrongly decoded docs
                # OPTIMIZE: here a screen refresh could be needed. some chars are
                #   left in the buffer when scrolling
                log.warning("decode error:", error)
            except Exception:
                # this exception tell's us to end the emulator:
                # throwed when exiting the command
                loop.remove_reader(self.p_out)
                self.data_or_disconnect = None
                self.event.set()

        loop.add_reader(self.p_out, on_output)
        await self.send_queue.put(["setup", {}])
        try:
            while True:
                msg = await self.recv_queue.get()
                if msg[0] == "stdin":
                    self.p_out.write(msg[1].encode())
                elif msg[0] == "set_size":
                    winsize = struct.pack("HH", msg[1], msg[2])
                    fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
                elif msg[0] == "click":
                    x = msg[1] + 1
                    y = msg[2] + 1
                    button = msg[3]

                    if button == 1:
                        self.p_out.write(f"\x1b[<0;{x};{y}M".encode())
                        self.p_out.write(f"\x1b[<0;{x};{y}m".encode())
                elif msg[0] == "scroll":
                    x = msg[2] + 1
                    y = msg[3] + 1

                    if msg[1] == "up":
                        self.p_out.write(f"\x1b[<64;{x};{y}M".encode())
                    if msg[1] == "down":
                        self.p_out.write(f"\x1b[<65;{x};{y}M".encode())
        except asyncio.CancelledError:
            # log.warning("TerminalEmulator._run cancelled")
            pass

    async def _send_data(self):
        try:
            while True:
                await self.event.wait()
                self.event.clear()
                if self.data_or_disconnect is not None:
                    await self.send_queue.put(["stdout", self.data_or_disconnect])
                else:
                    await self.send_queue.put(["disconnect", 1])
        except asyncio.CancelledError:
            # log.warning("TerminalEmulator._send_data cancelled")
            # os.close(self.fd)  # does not fix the error above, maybe too late
            pass
