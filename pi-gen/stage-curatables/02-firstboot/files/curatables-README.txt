Curatables: help files on this SD card
======================================

Everything is normally done from the parent dashboard:
open  http://curatables.local  on a phone, tablet or computer on the
same network as the box.

If you cannot reach the dashboard, these files on this card (the part
your computer can open) help. Turn the box off first, put the card in
your computer, make the change, put the card back and turn the box on.

curatables-wifi.txt
    Set up or change Wi-Fi. Open it and follow the instructions inside.
    curatables-firstboot.log, next to it, says what happened.

curatables-reset.txt
    Forgot the parent dashboard password? Create an empty file with this
    name. On the next start the password is cleared; open the dashboard
    within 30 minutes to choose a new one (and a new password for the
    box's Linux login). Your videos, kids and settings are kept.

curatables-diagnose.txt
    Something is wrong and someone is helping you? Create an empty file
    with this name and start the box. After a few minutes, turn it off
    again: the card now has curatables-diagnostics-report.txt to send
    them. Passwords are removed from it, but it names your networks.

ssh
    An empty file named just "ssh" turns on SSH logins (the remote
    command line) for people who use it.

Each file except ssh is used once and then renamed to ...-done.txt.
Whoever holds this card can change anything on the box, so keep it with
the box, out of reach.
