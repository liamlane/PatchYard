# Running Patchyard in a Proxmox VM

Use a VM, not an LXC container. Patchyard creates network namespaces, veth pairs, VRFs and Open vSwitch bridges in the kernel. An unprivileged LXC can't do that, and a privileged one would do it on the Proxmox host's own kernel.

## 1. Create the VM

From the Proxmox web UI (Create VM):

| Tab | Setting |
| --- | --- |
| General | name `patchyard` |
| OS | Ubuntu Server 24.04 LTS ISO (`ubuntu-24.04.x-live-server-amd64.iso`) |
| System | Machine `q35`, BIOS `OVMF (UEFI)` with an EFI disk, Qemu Agent ticked |
| Disks | 40 GB, VirtIO SCSI single, Discard ticked if the storage is thin |
| CPU | 4 cores, type `host` (gives the guest the host's CPU features) |
| Memory | 8192 MB (4096 minimum); ballooning off is the safer choice for labs |
| Network | your LAN bridge (e.g. `vmbr0`), model VirtIO |

Or from the Proxmox shell:

```bash
qm create 120 --name patchyard --ostype l26 --machine q35 --bios ovmf --efidisk0 local-lvm:1,efitype=4m \
  --cores 4 --cpu host --memory 8192 --balloon 0 --agent enabled=1 \
  --scsihw virtio-scsi-single --scsi0 local-lvm:40,discard=on,iothread=1 \
  --net0 virtio,bridge=vmbr0 --ide2 local:iso/ubuntu-24.04.3-live-server-amd64.iso,media=cdrom --boot order='scsi0;ide2'
qm start 120
```

Change `120`, `local-lvm`, `local:iso/…` and `vmbr0` to match your node.

## 2. Install Ubuntu

Do a normal Ubuntu Server install:

- **Profile:** use a static address or a DHCP reservation, so the URL doesn't move.
- **SSH:** tick *Install OpenSSH server*.
- **Snaps:** don't tick the Docker snap. The Patchyard installer adds Docker Engine itself.

After the first boot:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y qemu-guest-agent linux-modules-extra-$(uname -r)
sudo reboot
```

## 3. Install Patchyard

```bash
curl -fsSL https://raw.githubusercontent.com/liamlane/patchyard/main/deploy/install.sh | sudo bash
```

Open `http://<vm-ip>:8080`, log in with the password you set, and pick a template from the Lab manager.

## Notes

- **Nested virtualisation** isn't needed. Every lab device is a container, not a VM.
- **Lab traffic** stays inside the VM. Only the web UI is reachable from your LAN. Lab devices get to the internet (for Alpine packages) through the VM's Docker NAT.
- **Backups:** back up the VM, or just `/opt/patchyard`, which holds labs, configs, snapshots, settings and metrics. A running lab's state is its saved configs, so use *Save running configs* before a backup.
- **Sizing:** idle labs are cheap. An FRR router container uses tens of MB, and the 27-device MPLS template runs comfortably in 6 GB.
- **Memory:** if you enable ballooning, give the VM a high enough minimum. Containers killed by memory pressure look like "device stopped" alerts.
