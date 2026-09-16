# Polaris / NORTHSTORM (CMS launch binding)

The RAES-native CMS launch binding for the Polaris / NORTHSTORM range on the GCE
range-cell path. It declares the two guests Shifter provisions:

- **a14-kali** — the participant Kali docker-host. The Boreas a0-a16 compose
  stack (web, mail, AD-adjacent services, SCADA/OT, splice/bunker) is baked into
  the `polaris-vm` image; at provision time the host's compose override is
  rewired to this range's DC IP and participant key and the stack is brought up.
- **dc01** — a pre-promoted Windows Server Active Directory domain controller
  (BOREAS / boreas.local), baked into the `polaris-dc` image.

Scenario content (flags, planted data, challenge topology) ships in the baked
images and the authoritative Polaris pack, not in this launch binding. This pack
carries only the provider-neutral launch topology so the range stands up through
`create_range` and the CTF event flow.
