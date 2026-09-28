# @DISTRO_NAME@ shell niceties — edit freely (sourced by ~/.bashrc).

alias neofetch='horizonfetch'
alias fetch='horizonfetch'
alias update='horizon update'

# Modern replacements, only if installed
if command -v eza >/dev/null; then
    alias ls='eza --group-directories-first'
    alias ll='eza -l --git --group-directories-first --icons=auto'
    alias la='eza -la --git --group-directories-first --icons=auto'
    alias tree='eza --tree'
fi
command -v batcat >/dev/null && alias bat='batcat'

# Fuzzy history search with Ctrl+R
[ -f /usr/share/doc/fzf/examples/key-bindings.bash ] && . /usr/share/doc/fzf/examples/key-bindings.bash

# Pretty prompt (config: ~/.config/starship.toml)
if command -v starship >/dev/null && [ "${TERM:-dumb}" != dumb ]; then
    eval "$(starship init bash)"
fi

# Show system info in new terminal windows (remove to disable)
if [ -z "${HORIZON_FETCH_SHOWN:-}" ] && [ -t 1 ] && command -v horizonfetch >/dev/null; then
    export HORIZON_FETCH_SHOWN=1
    horizonfetch --small
fi
