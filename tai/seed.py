"""Small universal seed corpus.

This is not intended to contain every CLI. It provides useful, safe
completions for commands that are almost always present, even when the user
has never used the command before. `tai discover` adds installed-tool help and
man-derived candidates to this base.
"""

SEED_COMMANDS = [
    "ls -la", "ls -l", "ls -1", "ls --all", "ls --color=auto",
    "pwd", "cd ..", "cd -",
    "cat file", "less file", "head -n 20 file", "tail -n 20 file",
    "grep -n pattern file", "rg pattern", "find . -type f",
    "mkdir -p directory", "touch file", "cp -r source destination",
    "mv source destination", "rm -i file", "rm -r directory",
    "chmod +x script", "chmod 644 file", "file path", "du -sh .",
    "df -h", "free -h", "ps aux", "kill -TERM PID", "top",
    "curl -sS URL", "wget URL", "ssh host", "scp file host:path",
    "git status", "git diff", "git log --oneline", "git switch branch",
    "git checkout branch", "git branch --show-current", "git add .",
    "git commit -m message", "git push", "git pull --rebase",
    "systemctl restart nginx", "systemctl status nginx", "systemctl enable nginx",
    "docker ps", "docker images", "docker compose up -d",
    "docker compose down", "docker compose logs -f", "docker build .",
    "kubectl get pods", "kubectl get pods -A", "kubectl describe pod NAME",
    "make", "make build", "make test", "npm run dev", "npm test",
    "python3 -m pytest", "python3 -m venv .venv",
]
