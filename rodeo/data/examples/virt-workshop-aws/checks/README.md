# Self-checks

One script per exercise that has concrete, checkable state (1-3). Exercises
4-7 print a pass with a pointer to what to look for in the UI instead —
adapted straight from [suse-virt-rodeo](https://github.com/avaleror/suse-virt-rodeo)'s
own Instruqt checks, where those chapters are deliberately left unscored
(everything they need is self-provisioned, and nothing later in the track
reads back state you create in them).

The scripts live under `/root/rodeo-lab/checks/` on the deployed host,
which only `sudo` can read. `cd` there in the *same* sudo shell, not before
it. A plain `cd /root/rodeo-lab && sudo ...` hits the same permission
denied a plain `ls` would:

```bash
sudo bash -c 'cd /root/rodeo-lab && ./checks/check-exercise-1.sh'
```

Each script defaults `KUBECONFIG` to `~/.rodeo/harvester-kubeconfig` under
whichever user ran `rodeo up` (the same invoking-user resolution rodeo-cli
itself uses, so `sudo` here doesn't point it at root's own, nonexistent
copy). Override with `sudo bash -c 'KUBECONFIG=/path/to/kubeconfig ./checks/check-exercise-N.sh'`
if yours lives elsewhere.
