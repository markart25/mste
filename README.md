# mste (mark's simple text editor)

A small nano-like terminal text editor written in Python using the standard library `curses` module. Zero dependencies.

## Features

- Familiar nano-style keybindings (`Ctrl-S` save, `Ctrl-X` quit, etc.)
- Cut/paste a line, incremental search
- Save / Save As, dirty-buffer warnings on quit
- Title bar, status line, help bar
- Works on any Linux/macOS terminal that supports curses

## Install

### From source

```sh
git clone https://github.com/markart25/mste.git
cd mste
pip install --user .
```


This puts a `mste` command on your `$PATH`.

### Arch Linux (AUR) 

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
| `Ctrl-Z` | Undo              |
| `Ctrl-Y` | Redo              |
| `Ctrl-K` | Cut line / selection |
| `Ctrl-U` | Paste             |
| `Ctrl-W` | Search            |
| `Ctrl-G` | Help              |

## License

MIT
