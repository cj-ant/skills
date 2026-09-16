import sys

from .agent import Agent

if __name__ == "__main__":
    Agent().run(" ".join(sys.argv[1:]) or "Run the tests and fix what fails.")
