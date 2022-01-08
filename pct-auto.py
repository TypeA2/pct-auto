#!/usr/bin/python3
import sys
import re
import json
import socket
import os
import subprocess
from pathlib import Path
executable = Path(sys.argv[0]).name

if os.getuid() != 0:
    print(f"{executable} requires root access")
    exit(1)

if "/etc/pve" not in Path("/proc/mounts").read_text():
    print("ERROR: /etc/pve is not mounted")
    exit(1)

nodes = set()
vms = {}
with open("/etc/pve/.vmlist") as f:
    vmlist = json.load(f)

    for vm, info in vmlist["ids"].items():
        nodes.add(info["node"])
        vms[vm] = info["node"]

if len(nodes) == 0:
    print("ERROR: no nodes present")
    exit(1)

this_node = socket.gethostname()

dump_script = """
#!/usr/bin/perl -T
use PVE::CLI::pct;

# Iterate over all defined commands
while (my($k, $v) = each %$PVE::CLI::pct::cmddef) {
    # Skip any aliases
    next if (ref($v) ne 'ARRAY');

    print $k . '=' . join(',', @{$v->[2]}) . "\n";
}
"""

# Gather native pct's capabilities
actions = {}
argless = { "list" }
for cmd in subprocess.run("perl", text=True, input=dump_script, stdout=subprocess.PIPE).stdout.rstrip().split("\n"):
    name, args = cmd.split("=")
    args = tuple(arg for arg in args.split(",") if arg);

    # Only actions that have a vmid parameter
    if "vmid" in args or name in argless:
        actions[name] = args

actions = dict(sorted(actions.items()))

def usage():
    print(f"Usage: {executable} <COMMAND> [ARGS] [OPTIONS]\n")

    for name, args in actions.items():
        print(f"    {executable} {name}", end="")

        for arg in args:
            print(f" <{arg}>", end="")
        
        print()

if len(sys.argv) < 2:
    print("ERROR: no command specified")
    usage()
    exit(1)

cmd = sys.argv[1]
if cmd not in actions:
    print(f"ERROR: unknown command '{executable} {cmd}'")
    usage()
    exit(1)

if cmd in argless:
    if len(sys.argv) > 2:
        print("ERROR: too many arguments")
        exit(1)

    # Perform argless commands on all nodes, with some formatting

    # Longest node name + 3, at least 11
    node_col_width = max(9, max(map(lambda e: len(e), nodes))) + 3

    # First on our own node, then all other nodes
    for node in [this_node, *nodes.difference({this_node})]:
        argv = ["pct", cmd]
        if node != this_node:
            argv[0:0] = ["ssh", f"root@{node}"]

        res = subprocess.run(argv, text=True, stdout=subprocess.PIPE)

        if node == this_node:
            print("Node".ljust(node_col_width), end="")
            print(res.stdout.split("\n")[0])

        for line in res.stdout.rstrip().split("\n")[1:]:
            print(node.ljust(node_col_width), end="")
            print(line)
else:
    # If there's arguments, the first argument will be the vmid. Find this, and execute on the specified node
    vmid = sys.argv[2]
    if not vmid.isdigit():
        # Resolve vmid from vm name
        # Check every node's shared config
        for node in nodes:
            conf: Path
            for conf in (Path("/etc/pve/nodes") / node / "lxc").glob("*.conf"):
                # Unknown VM, shouldn't happen
                if conf.stem not in vms:
                    continue

                with conf.open() as c:
                    for line in c:
                        line = line.rstrip()
                        if line.startswith("hostname") and line.split(" ")[1] == vmid:
                            vmid = conf.stem
                            break
                    else:
                        continue
                    break
            else:
                continue
            break

        if not vmid.isdigit():
            print(f"ERROR: unknown vm name: '{vmid}'")
            exit(1)

    target_node = vms[vmid]

    # Forward arguments
    binary = "/usr/sbin/pct"
    argv = ["pct", cmd, vmid, *sys.argv[3:]]
    if target_node != this_node:
        binary = "/usr/bin/ssh"
        argv[0:0] = ["ssh", "-t", "-o", "LogLevel=QUIET", f"root@{target_node}"]

    os.execvp(binary, argv)
