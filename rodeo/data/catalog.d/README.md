# Rodeo catalog

One file per published rodeo, `<name>.yaml` (see `rodeo/catalog.py`):

```yaml
name: ride
title: Ride the range
repo: https://github.com/<owner>/<repo>
ref: main
engine: rancher
languages: [en, es]
```

`rodeo up --profile <name>` fetches the repository and installs it as a custom
profile; `rodeo profiles` lists the catalog. The Rodeo Builder's **Publish**
opens the pull request that adds the file.
