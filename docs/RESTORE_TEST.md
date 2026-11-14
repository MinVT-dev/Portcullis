# Testing a backup restore

Restoring from Discord hasn't been seen working on a real panel yet. This checklist tests it on a
server you can lose, so a wrong restore costs nothing. It takes about 20 minutes.

Never run it on a server people play on. A restore rolls the whole server back.

## Before you start

- [ ] Make a throwaway server in AMP. A small one is quickest: a vanilla Minecraft server with a fresh
      world is fine. Name it something you can't confuse with a real server, such as `restore-test`.
- [ ] Start it once so it has a world, then stop it.
- [ ] Wait a minute for Portcullis to see it (`/server list` shows it).
- [ ] Have two Discord accounts ready: yours (Manage Server and an operator role) and a second one
      with an operator role but **without** Manage Server.

## 1. Make a known starting point

- [ ] In AMP, take the first backup of `restore-test` by hand. Name it `A`.
- [ ] Change something you can check later: in AMP's file manager, create `restore-marker.txt` in the
      server's folder.
- [ ] `/backups restore-test`, press **Make a backup** and confirm. Wait a minute and run
      `/backups restore-test` again: a backup named `Discord <date>` is listed. Call it `B`.

## 2. Who may restore

- [ ] With the second account (operator, no Manage Server): `/backups restore-test`. There is no
      **Restore one of these…** picker.
- [ ] With your account: the picker is there.
- [ ] Pick `A`. The message names `restore-test` and `A` with its date, and says it will ask for the
      server's name. Nothing has happened yet: AMP's backup list is unchanged.
- [ ] Press **Restore…** and type the wrong name, for example `restore-tes`. The bot says nothing was
      restored. Check AMP: nothing changed.

## 3. The restore itself

- [ ] Pick `A` again, press **Restore…**, type `restore-test` and submit.
- [ ] Within a few seconds a message in the channel says **Restoring restore-test to A**, with your
      name.
- [ ] Within a few minutes a second message says either **is restored to A** or that it failed.
      Write down which.
- [ ] The same message says AMP saved the state from before as a backup (its name is shown), or
      warns that it couldn't find one. Write down which.
- [ ] In AMP, the backup list has a new PreRestore backup, and the timeline's current position is `A`.
- [ ] `restore-marker.txt` is gone.

## 4. Undo it

- [ ] Restore `B` or the PreRestore backup the same way. `restore-marker.txt` is back.

## 5. Errors

- [ ] Start a restore and, while AMP is still working, start another one. The bot says AMP is already
      restoring the server, or AMP's "busy with another backup job" message. It never shows a raw
      error code alone.
- [ ] `/audit` lists `backup.restore` and `backup.restore.finished` for each attempt, and the refused
      one from step 2 as `denied`.

## 6. Clean up

- [ ] Delete `restore-test` in AMP.

## What to send back

The two lines you wrote down in section 3, plus anything that looked wrong. Leave out the panel's
address, tokens and screenshots of AMP's settings pages.
