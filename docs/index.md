---
layout: home
title: Terminal AI Helper — your terminal, one step ahead
description: TAI — a shell plugin for bash and zsh that learns the commands you actually run and suggests them as you type. Ghost hints, ranked menus, typo correction. Local, offline, no daemon.

hero:
  name: "TAI"
  text: "Terminal AI Helper"
  tagline: A shell plugin for bash and zsh that learns the commands you actually run and suggests them as you type — no server, no daemon, no Python on your keystrokes. Everything runs on your machine, offline, stdlib only.
  actions:
    - theme: brand
      text: Install in one command
      link: /usage
    - theme: alt
      text: The keys
      link: /usage#the-keys
    - theme: alt
      text: GitHub
      link: https://github.com/mlibre/Terminal-AI-Helper

features:
  - title: Ghost hints as you type
    details: Type a few letters and the rest of the command you usually run appears in grey. One key takes it whole, another takes one word.
  - title: Your habits, ranked
    details: Press Tab and your own history answers — the lines you actually run, ranked by how often, how recently, in this directory, after what.
  - title: Typos answered by the habit they shadow
    details: A word that matches nothing brings up the learned lines it is a typo of — the one-off ranks below the habit, in the index itself.
  - title: The filesystem answers paths
    details: A line ending in a file is answered by what is actually there, newest first. A cd is answered by the directories that exist here.
  - title: It learns the tools it has never seen
    details: The first time you run an unknown tool, TAI reads its real --help in the background, so its subcommands and flags are known from then on.
  - title: A dashboard with the why
    details: tai web opens a localhost page of everything it learned — and every suggestion carries a why that opens the arithmetic behind its score.
---

<div class="demo">
  <img src="./demo.gif" alt="tai in action — the hint, the ranked list, the filesystem, a typo answered by the habit it shadows" loading="eager">
</div>

<div class="install-quick">

```sh
curl -fsSL https://raw.githubusercontent.com/mlibre/Terminal-AI-Helper/main/install.sh | bash
```

</div>

Start with [the keys](/usage), or read [how it is built](/architecture). Your
history never leaves the machine — secrets and stray keys are filtered out
before anything is stored — and uninstall is one command that removes
everything.

<style>
.demo { margin: 2.5rem 0 1rem; }
.demo img { width: 100%; border-radius: 12px; border: 1px solid var(--vp-c-divider); }
.install-quick { margin: 1rem 0 2rem; }
</style>
