# skills

Skills for Claude Code that I share with the community.

## Layout

Each skill lives in its own directory under `skills/` with a `SKILL.md` file:

```
skills/
  hello-world/
    SKILL.md
```

## Install a skill

Copy the skill directory into your personal skills folder:

```bash
git clone https://github.com/cj-ant/skills.git
cp -r skills/skills/hello-world ~/.claude/skills/
```

Or copy it into a project at `.claude/skills/` to share it with everyone who works in that repo.

Start a new Claude Code session and run `/hello-world` to confirm it loaded.

## Skills

| Skill | What it does |
| --- | --- |
| `hello-world` | Test skill that confirms skills from this repo load correctly. |
