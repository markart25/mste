# mste (marks simple text editor)

A small nano-like terminal text editor written in Python using the standard library `curses` module. Zero dependencies.

## Features

- Familiar nano-style keybindings (`Ctrl-S` save, `Ctrl-X` quit, etc.)
- Line number gutter with current-line highlight
- Select-all, cut/paste, incremental search
- Save / Save As, dirty-buffer warnings on quit
- Works on any Linux/macOS terminal that supports curses

## Install

### From source

```sh
git clone https://github.com/markart25/mste.git
cd mste
pipx install --editable .
```

### Arch Linux (AUR) (not yet mabye soon)

```sh
yay -S mste
```

## Usage

```sh
mste              # start with empty buffer
mste notes.txt    # open a file
```

## Keybindings

| Key      | Action            |
|----------|-------------------|
| `Ctrl-S` | Save              |
| `Ctrl-O` | Save As           |
| `Ctrl-X` | Quit              |
| `Ctrl-A` | Select all        |
| `Ctrl-K` | Cut line / selection |
| `Ctrl-U` | Paste             |
| `Ctrl-W` | Search            |
| `Ctrl-G` | Help              |

## License

MIT
