"""diskkit: the stdlib-only engine behind the Diskwise plugin.

Every analysis command is read-only. Only `remove`, `purge` and `backup --move`
change the disk, and they go through the guards in `safety.py`.
"""

__version__ = "0.1.0"
