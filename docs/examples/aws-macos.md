# Example: AWS from a Mac, step by step

This example starts from a Mac with nothing installed and ends with the `rancher` profile running on AWS: Rancher Prime on K3s plus three downstream clusters it manages (single-node K3s, single-node RKE2, 3-node RKE2). The Mac is only the control machine. The lab runs as nested VMs on one EC2 host. Any other profile works the same way: change `--profile`.

## What you need

| Requirement | Why | Check |
|---|---|---|
| macOS with git and curl | The installer clones the repo and fetches its own Python if needed | `git --version` |
| Homebrew | Installs the AWS CLI and tmux | `brew --version` |
| AWS CLI 2.32.0 or later | Older versions have no `aws login` | `aws --version` |
| tmux (recommended) | Keeps the local `rodeo up` session alive if the terminal closes | `tmux -V` |
| AWS console access | `aws login` reuses your console session (SUSE accounts: sign in with SUSE ID) | You can open the AWS console |

No Python install and no AWS access keys are needed.

Paste the command blocks below as they are. zsh does not treat `#` lines as comments when pasted interactively, so keep comments out of what you paste.

## Step 1: command line tools

```bash
xcode-select --install
```

If they are already installed, move on.

## Step 2: Homebrew

Skip if `brew --version` works.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## Step 3: AWS CLI and tmux

```bash
brew install awscli tmux
brew upgrade awscli
aws --version
```

`brew upgrade` only matters if the CLI was already installed. The version must be 2.32.0 or later.

## Step 4: install rodeo-cli

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

On a Mac the installer sets rodeo up as a control machine: no root, installed in `~/.local/share/rodeo-cli`, command in `~/.local/bin/rodeo`, with the `[aws]` extra. If the shell does not find `rodeo`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
rodeo --help
```

To update later: `rodeo self-update`.

## Step 5: log in to AWS

Sign in to the AWS console in the browser first, then:

```bash
aws login
aws sts get-caller-identity
```

The first time, `aws login` asks for a default region if none is configured: enter `eu-north-1`, the region rodeo uses. Then it opens a browser page to confirm. `get-caller-identity` shows the account and role: check it before you create anything. The session lasts up to 12 hours, depending on the role. When it expires, run `aws login` again. The IAM principal needs the `SignInLocalDevelopmentAccess` managed policy (root users do not).

After the first login, recent AWS CLI versions offer to configure AWS skills and the AWS MCP server for AI coding agents. The lab does not need them: answer `n` to each prompt.

## Step 6: deploy

```bash
rodeo up --profile rancher --target aws --yes
```

rodeo picks the region (`eu-north-1`, or `$RODEO_AWS_REGION`), the default VPC public subnet and the profile's recommended instance, and writes them to the lab plan. It creates an SSH key and a security group that admits only your current public IP, launches the host, and builds the lab there in tmux. It ends by printing the Rancher URL and credentials. Expect 20 to 40 minutes for `rancher`.

Local files it creates:

| Path | Content |
|---|---|
| `~/rodeo-labs/rancher/` | Lab plan with region, subnet and instance. `rodeo destroy` needs it |
| `~/.rodeo/secrets.yaml` | Generated passwords (chmod 600) |
| `~/.rodeo/ssh/labs/rancher/` | This lab's own SSH key for the host and the lab VMs. Generated for this deployment and deleted by `rodeo destroy --cloud` |

If the terminal closes during the deploy: `tmux attach -t rodeo-rancher`.

## Step 7: log in to Rancher

- URL: `https://<host-public-ip>:30002`
- User: `admin`
- Password: `rancher_admin_password` in `~/.rodeo/secrets.yaml`

`rodeo status` prints them again. On first login, confirm the server URL with the host IP. For SSH and day-2 commands, see the [Rancher profile guide](../guide-rancher.md#day-2-operations). Run them from `~/rodeo-labs/rancher` or pass `--config-dir ~/rodeo-labs/rancher`.

## Step 8: tear down

The host terminates itself after `provider.ttl_hours` (default 6, see the [dead-man switch](../reference/plan.md#provider-when-deployment_target-aws)). To delete it sooner:

```bash
rodeo destroy --cloud --yes --config-dir ~/rodeo-labs/rancher
```


## SSH key and shared accounts

Each deployment generates its own SSH key in `~/.rodeo/ssh/labs/<lab>/` and imports it as an EC2 key pair named `rodeo-<lab>-<random>`. Two users in the same AWS account never share or overwrite a key pair. `rodeo destroy --cloud` deletes the key pair and the local key. If the dead-man switch ends the lab first, the next `rodeo up` cleans them up.

The lab name still has to be yours. If someone else in the account has a live lab with the same plan name, `rodeo up` stops with:

```
lab 'rancher' already has instances in AWS (...) launched with key pair ..., whose private key is not on this machine
```

Do not destroy that lab: it is another user's. Change `name:` in `~/rodeo-labs/rancher/rodeo-plan.yaml` (for example `rancher-yourname`) and run `rodeo up` again.

## Common problems

| Symptom | Fix |
|---|---|
| `aws: unknown command 'login'` | AWS CLI older than 2.32.0: `brew upgrade awscli` |
| `session has expired` or `CreateOAuth2Token` | Run `aws login` again. A one-off `CreateOAuth2Token` error can just need a re-run |
| `MissingDependencyException` | The `[aws]` extra is missing: run the installer again |
| `no default VPC in <region>` | `export RODEO_AWS_REGION=<region with a default VPC>` and run `rodeo up` again |
| Rancher UI or SSH times out | Your public IP changed (new Wi-Fi, VPN). Re-run the `rodeo up` command: it moves the rule to your new IP and skips finished phases |
| `already has instances in AWS ... not on this machine` | Another user has a live lab with your plan name. See the SSH key section above |
| `rodeo: command not found` | Add `~/.local/bin` to the PATH (step 4) |

More in the [runbook](../runbook.md).
