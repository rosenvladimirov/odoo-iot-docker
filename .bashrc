# ~/.bashrc: executed by bash(1) for non-login shells.
# see /usr/share/doc/bash/examples/startup-files (in the package bash-doc)
# for examples

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# don't put duplicate lines or lines starting with space in the history.
# See bash(1) for more options
HISTCONTROL=ignoreboth

# append to the history file, don't overwrite it
shopt -s histappend

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=1000
HISTFILESIZE=2000

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
#[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color|*-256color) color_prompt=yes;;
esac

# uncomment for a colored prompt, if the terminal has the capability; turned
# off by default to not distract the user: the focus in a terminal window
# should be on the output of commands, not on the prompt
force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
	# We have color support; assume it's compliant with Ecma-48
	# (ISO/IEC-6429). (Lack of such support is extremely rare, and such
	# a case would tend to support setf rather than setaf.)
	color_prompt=yes
    else
	color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w \$\[\033[00m\] '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi
unset color_prompt force_color_prompt

# If this is an xterm set the title to user@host:dir
case "$TERM" in
xterm*|rxvt*)
    PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
    ;;
*)
    ;;
esac

# enable color support of ls and also add handy aliases
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# some more ls aliases
#alias ll='ls -l'
#alias la='ls -A'
#alias l='ls -CF'

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if ! shopt -oq posix; then
  if [ -f /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion
  elif [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
  fi
fi
alias ll='ls -al'
alias odoo='sudo systemctl stop odoo; sudo -u odoo /usr/bin/python3 /home/pi/odoo/odoo-bin --config /home/pi/odoo.conf'
alias odoo_logs='less -R +F /var/log/odoo/odoo-server.log'
alias odoo_conf='cat /home/pi/odoo.conf'
alias install='sudo chroot /root_bypass_ramdisks/'
alias blackbox='ls /dev/serial/by-path/'
alias nano='sudo -u odoo nano -l'
alias vim='sudo -u odoo vim -u /home/pi/.vimrc'
alias odoo_luxe='printf " ______\n< Luxe >\n ------\n        \   ^__^\n         \  (oo)\_______\n            (__)\       )\/\ \n                ||----w |\n                ||     ||\n"'
alias odoo_start='sudo systemctl start odoo'
alias odoo_stop='sudo systemctl stop odoo'
alias odoo_restart='sudo systemctl restart odoo'

odoo_help() {
  echo '-------------------------------'
  echo ' Welcome to Odoo IoT Box tools'
  echo '-------------------------------'
  echo ''
  echo 'odoo                  Starts/Restarts Odoo server manually (not through odoo.service)'
  echo 'odoo_logs             Displays Odoo server logs in real time'
  echo 'odoo_conf             Displays Odoo configuration file content'
  echo 'install               Bypasses ramdisks to allow package installation'
  echo 'blackbox              Lists all serial connected devices'
  echo 'odoo_start            Starts Odoo service'
  echo 'odoo_stop             Stops Odoo service'
  echo 'odoo_restart          Restarts Odoo service'
  echo 'odoo_dev <branch>     Resets Odoo on the specified branch from odoo-dev repository'
  echo 'odoo_origin <branch>  Resets Odoo on the specified branch from the odoo repository'
  echo 'devtools              Enables/Disables specific functions for development (more help with devtools help)'
  echo ''
  echo 'Odoo IoT online help: <https://www.odoo.com/documentation/latest/applications/general/iot.html>'
}

odoo_dev() {
  if [ -z "$1" ]; then
    odoo_help
    return
  fi
  pwd=$(pwd)
  cd /home/pi/odoo
  sudo -u odoo git remote add dev https://github.com/odoo-dev/odoo.git
  sudo -u odoo git fetch dev $1 --depth=1 --prune
  sudo -u odoo git reset --hard FETCH_HEAD
  sudo -u odoo git branch -m $1
  sudo -u odoo pip3 install -r /home/pi/odoo/setup/iot_box_builder/configuration/requirements.txt --break-system-package
  sudo chroot /root_bypass_ramdisks /bin/bash -c "export DEBIAN_FRONTEND=noninteractive && xargs apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install < /home/pi/odoo/setup/iot_box_builder/configuration/packages.txt"
  cd $pwd
}

odoo_origin() {
  if [ -z "$1" ]; then
    odoo_help
    return
  fi
  pwd=$(pwd)
  cd /home/pi/odoo
  sudo -u odoo git remote set-url origin https://github.com/odoo/odoo.git  # ensure odoo repository
  sudo -u odoo git fetch origin $1 --depth=1 --prune
  sudo -u odoo git reset --hard FETCH_HEAD
  sudo -u odoo git branch -m $1
  sudo chroot /root_bypass_ramdisks /bin/bash -c "export DEBIAN_FRONTEND=noninteractive && xargs apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install < /home/pi/odoo/setup/iot_box_builder/configuration/packages.txt"
  sudo -u odoo pip3 install -r /home/pi/odoo/setup/iot_box_builder/configuration/requirements.txt --break-system-package
  cd $pwd
}

pip() {
  if [[ -z "$1" || -z "$2" ]]; then
    odoo_help
    return 1
  fi
  additional_arg=""
  if [ "$1" == "install" ]; then
    additional_arg="--user"
  fi
  pip3 "$1" "$2" --break-system-package $additional_arg
}

devtools() {
  help_message() {
    echo 'Usage: devtools <enable/disable> <general/actions> [action name]'
    echo ''
    echo 'Only provide an action name if you want to enable/disable a specific device action.'
    echo 'If no action name is provided, all actions will be enabled/disabled.'
    echo 'To enable/disable multiple actions, enclose them in quotes separated by commas.'
  }
  case "$1" in
    enable|disable)
      case "$2" in
        general|actions|longpolling)
          if ! grep -q '^\[devtools\]' /home/pi/odoo.conf; then
            sudo -u odoo bash -c "printf '\n[devtools]\n' >> /home/pi/odoo.conf"
          fi
          if [ "$1" == "disable" ]; then
            value="${3:-*}" # Default to '*' if no action name is provided
            devtools enable "$2" # Remove action/general/longpolling from conf to avoid duplicate keys
            sudo sed -i "/^\[devtools\]/a\\$2 = $value" /home/pi/odoo.conf
          elif [ "$1" == "enable" ]; then
            sudo sed -i "/\[devtools\]/,/\[/{/$2 =/d}" /home/pi/odoo.conf
          fi
          ;;
        *)
          help_message
          return 1
          ;;
      esac
      ;;
    *)
      help_message
      return 1
      ;;
  esac
}

