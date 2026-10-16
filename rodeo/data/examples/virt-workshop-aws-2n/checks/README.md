# Self-checks

One script per exercise that has concrete, checkable state (1-3). Exercises
4-7 print a pass with a pointer to what to look for in the UI instead —
adapted straight from [suse-virt-rodeo](https://github.com/avaleror/suse-virt-rodeo)'s
own Instruqt checks, where those chapters are deliberately left unscored
(everything they need is self-provisioned, and nothing later in the track
reads back state you create in them).

Deploy copies the scripts to `/opt/suse-virt-workshop/checks/` on the lab
host (`custom/scripts/65-student-kit.sh`), readable by every user, so any
shell on the host can run them without sudo, including a claim-portal
student's:

```bash
/opt/suse-virt-workshop/checks/check-exercise-1.sh
```

They only need a kubeconfig for the Harvester cluster. In order, each script
uses `$KUBECONFIG` if set, then `~/.kube/config` (where Exercise 1.3 saves
the one downloaded from the Harvester UI), then `~/.rodeo/harvester-kubeconfig`
of whoever ran `rodeo up`. Without any of them, the script says so and stops.
