"""Module entry point avoids a Windows lumina.exe MCP launcher sharing the GUI name."""
from .cli import main

if __name__ == '__main__':
    main()
