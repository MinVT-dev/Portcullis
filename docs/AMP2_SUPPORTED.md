# What AMP 2 can run

A list of every game and service a user can run on AMP 2, with the player and chat flags Gatekeeper cares about. It exists so we know what the AMP 3 edition has to cope with. CubeCoders has said AMP 3 uses all existing AMP 2 templates, so this list is expected to carry over, but that is not confirmed until the AMP 3 beta is out.

**As of 2026-10-24.** Counted from the public `CubeCoders/AMPTemplates` repository at commit `aa71623` (2026-10-15), plus the forum list "Supported Applications Compatibility" (discourse.cubecoders.com/t/supported-applications-compatibility/1828). Both are CubeCoders' own sources.

## Summary

| | Count |
|---|---|
| Things a user can run | **246** |
| Games on dedicated modules (Minecraft Java Edition, Rust) | 2 |
| Games on the Generic module (templates) | 225 |
| Services and tools on the Generic module (templates) | 19 |

The forum list has 223 entries: the 2 dedicated modules and 221 Generic entries, where some entries cover more than one template (for example the two Tarkov Fika versions). The forum list is behind the repository by about ten templates, which are marked below.

What the 225 game templates tell AMP about players and chat:

| Template defines | Games |
|---|---|
| Join and leave patterns | 159 |
| Chat patterns | 59 |
| Both | 55 |
| Chat but no join/leave patterns | 4 (OpenRCT2, Squad, Squad 44, The Isle (Legacy)) |
| Neither | 62 |

How AMP talks to the game server (games only): console (STDIO) 137, Source RCON 48, log file 11, Quake RCON 11, Telnet 7, GoldSrc RCON 7, BattlEye RCON 4.

Operating systems (games only): Both 146, Both (Linux via Wine) 52, Both (Linux via Proton) 13, Linux only 9, Both (inferred) 2, Both (inferred) (Linux via Wine) 1, Windows only 1, ? (forum: deprecated on both) 1. "?" means the template does not say.

ARM support (games only, from the template): ? 140, No 59, Native 15, Emulated 11.

Only one template, Kaboom!, defines moderation actions (kick, ban, ban IP, smite). Every other game needs its own kick and ban commands if Gatekeeper wants to offer them.

## How to read the flags

- **Join/leave** and **Chat** are "yes" when the template contains a pattern AMP uses to spot player joins and leaves, or chat lines, in the console. "no" means the template defines none. It does not prove the game has no such feature, and a "yes" is not proof the pattern still matches after a game update. Templates get new patterns when a game update breaks them.
- **How AMP talks to it** is the control channel the template uses: the console, an RCON protocol, or a log file.
- **OS** and **ARM** come from the template metadata. "Both" means Windows and Linux hosts. "(Linux via Wine/Proton)" means the Linux build runs the Windows server under a compatibility layer, taken from the template's Linux executable and container image. "(inferred)" means the template has no OS field and the value was inferred from its executables. "?" means unknown.

## Games on dedicated modules

Dedicated modules add functionality beyond what the Generic module does. They are not in the `AMPTemplates` repository, and this scan did not look at what they add. Both are listed as supported on Windows and Linux.

| Game | Notes |
|---|---|
| Minecraft Java Edition | Dedicated module. Gatekeeper V3 has its own Minecraft code for this one. |
| Rust | Dedicated module. |

## Games on the Generic module (225)

| Game | Template | OS | Join/leave | Chat | How AMP talks to it | ARM | Notes |
|---|---|---|---|---|---|---|---|
| Abiotic Factor | `abiotic-factor` | Both (Linux via Wine) | yes | yes | console (STDIO) | ? |  |
| Alchemy Factory | `alchemy-factory` | Both (Linux via Wine) | yes | no | log file | ? |  |
| Alien Swarm: Reactive Drop | `alien-swarm-reactive-drop` | Both (inferred) (Linux via Wine) | yes | no | Source RCON | No | template only, not on the forum list yet |
| American Truck Simulator | `american-truck-simulator` | Both | yes | no | console (STDIO) | No | container required on Windows |
| ANEURISM IV | `aneurismiv` | Both | yes | no | console (STDIO) | ? | container required on Windows; template only, not on the forum list yet |
| Archean | `archean` | Both | yes | yes | console (STDIO) | ? |  |
| ARK: Survival Ascended | `ark-sa` | Both (Linux via Proton) | yes | yes | Source RCON | ? |  |
| ARK: Survival Ascended (Minimal) | `ark-sa-min` | Both (Linux via Proton) | yes | yes | Source RCON | ? |  |
| ARK: Survival Evolved | `ark-se` | Both | yes | yes | Source RCON | ? |  |
| ARK: Survival Evolved (Minimal with Server API) | `ark-seminapi` | Both (Linux via Wine) | yes | yes | Source RCON | ? |  |
| ARK: Survival Evolved (Minimal) | `ark-se-min` | Both | yes | yes | Source RCON | ? |  |
| Arma 3 | `arma3` | Both | yes | no | BattlEye RCON | ? |  |
| Arma Reforger | `arma-reforger` | Both | yes | no | BattlEye RCON | ? |  |
| ASKA | `aska` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Assetto Corsa | `assetto-corsa` | Both | yes | no | console (STDIO) | ? |  |
| Assetto Corsa Competizione | `assetto-corsa-comp` | Both (Linux via Wine) | yes | yes | console (STDIO) | ? |  |
| Astro Colony | `astro-colony` | Both | no | no | console (STDIO) | ? |  |
| Astroneer | `astroneer` | Both (Linux via Proton) | no | no | Telnet | Emulated |  |
| Avorion | `avorion` | Both | yes | yes | Source RCON | ? |  |
| Barotrauma | `barotrauma` | Both | yes | no | console (STDIO) | No |  |
| BeamMP | `beammp` | Both | yes | yes | console (STDIO) | Native |  |
| Beasts of Bermuda | `beasts-of-bermuda` | Both (Linux via Wine) | yes | yes | console (STDIO) | ? |  |
| Black Mesa | `black-mesa` | Both | yes | no | Source RCON | No |  |
| Blackwake | `blackwake` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Broke Protocol | `broke-protocol` | Both | yes | no | console (STDIO) | ? |  |
| Call of Duty 4: Modern Warfare | `call-of-duty4mw` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Call of Duty: Black Ops (Plutonium Mod) | `call-of-dutybo1` | Both (Linux via Wine) | no | no | Quake RCON | ? |  |
| Call of Duty: Black Ops II (Plutonium Mod) | `call-of-dutybo2` | Both (Linux via Wine) | no | no | Quake RCON | ? |  |
| Call of Duty: Modern Warfare 2 (2009) | `call-of-dutymw2` | Both (Linux via Wine) | yes | no | Quake RCON | ? |  |
| Call of Duty: Modern Warfare 3 (2011 - Plutonium Mod) | `call-of-dutymw3-plutonium` | Both (Linux via Wine) | no | no | Quake RCON | ? |  |
| Call of Duty: Modern Warfare 3 (2011) | `call-of-dutymw3` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Call of Duty: World at War (Plutonium Mod) | `call-of-dutywaw` | Both (Linux via Wine) | no | no | Quake RCON | ? |  |
| Carrier Command 2 | `carrier-command2` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Chivalry: Medieval Warfare | `chivalry-medieval-warfare` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Clone Hero | `clone-hero` | Both | no | no | console (STDIO) | Native |  |
| Colony Survival | `colony-survival` | Both | no | no | console (STDIO) | ? |  |
| Conan Exiles (Legacy) | `conan-exiles` | Both (Linux via Wine) | yes | yes | Source RCON | No |  |
| Conan Exiles Enhanced | `conan-exiles-enhanced` | Both | yes | yes | Source RCON | No |  |
| Contagion | `contagion` | Both (Linux via Wine) | yes | no | Source RCON | No |  |
| Core Keeper | `core-keeper` | Both | yes | no | console (STDIO) | ? |  |
| Counter-Strike 1.6 | `counter-strike16` | Both | yes | no | GoldSrc RCON | No |  |
| Counter-Strike 2 | `counter-strike2` | Both | yes | no | Source RCON | No |  |
| Counter-Strike: Condition Zero | `counter-strike-czero` | Both | yes | no | GoldSrc RCON | No |  |
| Counter-Strike: Global Offensive | `counter-strike-go` | Both | yes | no | Source RCON | No |  |
| Counter-Strike: Source | `counter-strike-source` | Both | yes | no | Source RCON | No |  |
| Craftopia | `craftopia` | Both | yes | yes | console (STDIO) | ? |  |
| Creativerse | `creativerse` | Both | no | no | console (STDIO) | ? |  |
| CryoFall | `cryofall` | Linux only | yes | no | console (STDIO) | No |  |
| Cube 2: Sauerbraten | `cube2-sauerbraten` | Linux only | yes | no | console (STDIO) | No | template only, not on the forum list yet |
| Cubic Odyssey | `cubic-odyssey` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Day of Defeat | `day-of-defeat` | Both | yes | no | GoldSrc RCON | No |  |
| Day of Defeat: Source | `day-of-defeat-source` | Both | yes | no | Source RCON | No |  |
| Day of Dragons | `day-of-dragons` | Both | no | no | console (STDIO) | ? |  |
| DayZ (Experimental) | `dayz-experimental` | Both | yes | yes | BattlEye RCON | ? |  |
| DayZ (Stable) | `dayz-original` | Both | yes | yes | BattlEye RCON | ? |  |
| Dead Matter | `deadmatter` | Both (Linux via Wine) | yes | no | console (STDIO) | No |  |
| DeadPoly | `deadpoly` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Desynced | `desynced` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Don't Starve Together | `dont-starve-together` | Both | yes | yes | console (STDIO) | ? |  |
| DOOM II (Zandronum Mod) | `doom2` | Both | no | no | console (STDIO) | No |  |
| Dota 2 | `dota2` | Both | yes | no | Source RCON | No |  |
| Dune Awakening | `duneawakening` | Linux only | yes | no | console (STDIO) | ? | template notes minor issues; Docker required; forum list says Docker required on Windows |
| E.Y.E: Divine Cybermancy | `eye-dc` | Both (Linux via Wine) | yes | no | Source RCON | No |  |
| Eco | `eco` | Both | yes | no | Source RCON | ? |  |
| Empyrion Galactic Survival | `empyrion-galactic-survival` | Both (Linux via Wine) | yes | yes | Telnet | ? |  |
| Enshrouded | `enshrouded` | Both (Linux via Proton) | yes | no | console (STDIO) | ? |  |
| ET: Legacy | `et-legacy` | Both | no | no | Quake RCON | No |  |
| Euro Truck Simulator 2 | `euro-truck-simulator-2` | Both | yes | no | console (STDIO) | No | container required on Windows |
| EXFIL | `exfil` | Both | yes | no | console (STDIO) | ? |  |
| Factorio | `factorio` | Both | yes | yes | Source RCON | Emulated |  |
| Farming Simulator 19 | `farming-simulator-19` | Both (Linux via Proton) | no | no | log file | No | non-commercial use only; container required on Windows |
| Farming Simulator 22 | `farming-simulator-22` | Both (Linux via Proton) | no | no | log file | No | non-commercial use only; container required on Windows |
| Farming Simulator 25 | `farming-simulator-25` | Both (Linux via Proton) | no | no | log file | No | non-commercial use only; container required on Windows |
| Fistful of Frags | `fistful-of-frags` | Both | yes | no | Source RCON | No |  |
| FiveM - Grand Theft Auto V Server | `fivem` | Both | no | no | Quake RCON | ? | non-commercial use only |
| FOUNDRY | `foundry` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Frozen Flame | `frozen-flame` | Both | yes | no | Source RCON | ? |  |
| Garry's Mod | `garrys-mod` | Both | yes | no | Source RCON | No |  |
| Garry's Mod (64 Bit) | `garrys-mod64` | Both | yes | no | Source RCON | No |  |
| Ground Branch | `ground-branch` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Half-Life | `half-life` | Both | yes | no | GoldSrc RCON | No |  |
| Half-Life 2: Deathmatch | `half-life2dm` | Both | yes | no | Source RCON | No |  |
| Half-Life Deathmatch: Source | `half-life-deathmatch-source` | Both | yes | no | Source RCON | No |  |
| Half-Life: Opposing Force | `half-life-opposing-force` | Both | yes | no | GoldSrc RCON | No |  |
| HumanitZ | `humanitz` | Both | yes | no | Source RCON | ? |  |
| Hurtworld | `hurtworld` | Both | no | no | console (STDIO) | ? |  |
| Hytale | `hytale` | Both | yes | yes | console (STDIO) | Native |  |
| Icarus | `icarus` | Both (Linux via Proton) | yes | no | log file | ? |  |
| Impostor - Among Us Server | `impostor` | Both | yes | no | console (STDIO) | Native |  |
| Insurgency Sandstorm | `insurgencysandstorm` | Both | yes | yes | Source RCON | ? |  |
| Just Cause 3 Multiplayer Mod | `jc3mp` | Both | yes | yes | console (STDIO) | ? |  |
| Kaboom! | `kaboom` | Linux only | yes | no | console (STDIO) | ? | forum list says Windows works through Docker |
| Killing Floor | `killing-floor` | Both | no | no | console (STDIO) | ? |  |
| Killing Floor 2 | `killing-floor-2` | Both | yes | no | console (STDIO) | ? |  |
| Last Oasis | `last-oasis` | Both | no | no | console (STDIO) | ? |  |
| Left 4 Dead | `left-4-dead` | Both | yes | no | Source RCON | No |  |
| Left 4 Dead 2 | `left-4-dead2` | Both | yes | no | Source RCON | No |  |
| Longvinter | `longvinter` | Windows only | no | no | console (STDIO) | ? |  |
| Longvinter (Linux Wine) | `longvinter-wine` | Linux only | no | no | console (STDIO) | ? |  |
| Luanti | `luanti` | Both | yes | yes | console (STDIO) | ? | template only, not on the forum list yet |
| Mindustry | `mindustry` | Both | yes | no | console (STDIO) | Native |  |
| Minecraft Bedrock | `minecraft-bedrock` | Both | yes | no | console (STDIO) | ? |  |
| Minetest (Legacy) | `minetest` | Both | yes | yes | console (STDIO) | ? |  |
| Miscreated | `miscreated` | Both (Linux via Wine) | no | no | log file | ? |  |
| Mordhau | `mordhau` | Both | no | no | Source RCON | ? |  |
| Mount & Blade II: Bannerlord | `mount-and-blade2` | Both | no | no | console (STDIO) | ? |  |
| Multi Theft Auto: San Andreas | `mta-sa` | Both | yes | no | console (STDIO) | Native |  |
| MX Bikes | `mxbikes` | Both (Linux via Wine) | no | no | console (STDIO) | No |  |
| Myth of Empires | `myth-of-empires` | Both (Linux via Proton) | yes | no | Source RCON | No |  |
| NEBULOUS: Fleet Command | `nebulous-fleet-command` | Linux only | yes | no | console (STDIO) | No |  |
| Necesse | `necesse` | Both | yes | yes | console (STDIO) | ? |  |
| Night of the Dead | `night-of-the-dead` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Nightingale | `nightingale` | Both | yes | no | console (STDIO) | ? |  |
| No More Room in Hell | `no-more-room-in-hell` | Both | yes | no | Source RCON | No |  |
| No One Survived | `no-one-survived` | Both (Linux via Wine) | yes | no | console (STDIO) | No |  |
| Nuclear Option | `nuclear-option` | Both | yes | no | console (STDIO) | No |  |
| Nukkit | `nukkit` | Both | yes | no | console (STDIO) | Native | template only, not on the forum list yet |
| Open World - RimWorld Server | `open-world` | Both | no | no | console (STDIO) | Native |  |
| open.mp - Grand Theft Auto: San Andreas Server | `open-mp` | Both | no | no | console (STDIO) | ? |  |
| OpenRA - Dune 2000 | `openra-dune-2000` | Both | yes | no | console (STDIO) | Emulated |  |
| OpenRA - Red Alert | `openra-red-alert` | Both | yes | no | console (STDIO) | Emulated |  |
| OpenRA - Tiberian Dawn | `openra-tiberian-dawn` | Both | yes | no | console (STDIO) | Emulated |  |
| OpenRCT2 | `openrct2` | Both | no | yes | console (STDIO) | ? |  |
| OpenStarbound | `openstarbound` | Both | yes | yes | Source RCON | ? |  |
| OpenTTD | `openttd` | Both | yes | no | console (STDIO) | ? | container required on Windows; forum list shows Linux only |
| Operation: Harsh Doorstop | `operation-harsh-doorstop` | Both | yes | no | Source RCON | ? |  |
| Palworld | `palworld` | Both | yes | yes | console (STDIO) | Emulated |  |
| Palworld (Modded) | `palworld-modded` | Both (Linux via Wine) | yes | no | console (STDIO) | Emulated |  |
| Path of Titans | `path-of-titans` | Both | yes | yes | Source RCON | ? |  |
| Pavlov VR | `pavlov-vr` | Linux only | yes | no | Telnet | ? |  |
| Pirates, Vikings, & Knights II | `pirates-vikings-knights2` | Both | yes | no | Source RCON | No | template only, not on the forum list yet |
| PixARK | `pixark` | Both (Linux via Proton) | yes | yes | Source RCON | ? |  |
| Plains of Pain | `plains-of-pain` | Both | no | no | console (STDIO) | ? | template only, not on the forum list yet |
| PocketMine-MP | `pocketmine-mp` | Both | yes | no | console (STDIO) | No | template only, not on the forum list yet |
| Portal Knights | `portal-knights` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Pre-Fortress 2 | `pre-fortress2` | Both | yes | no | Source RCON | No |  |
| Project 5: Sightseer | `project5-sightseer` | Both | yes | no | console (STDIO) | ? |  |
| Project Zomboid | `project-zomboid` | Both | yes | no | console (STDIO) | ? |  |
| Puck | `puck` | Both | yes | no | console (STDIO) | ? |  |
| Pumpkin | `pumpkin` | Both | yes | yes | console (STDIO) | Native | pre-release template |
| Quake III Arena | `quake3-arena` | Both | no | no | Quake RCON | No |  |
| Quake Live | `quake-live` | Linux only | no | no | console (STDIO) | No |  |
| RAGE:MP - Grand Theft Auto V Server | `ragemp` | Both | no | no | console (STDIO) | ? | non-commercial use only |
| RedM - Red Dead Redemption 2 Server | `redm` | Both | no | no | Quake RCON | ? | non-commercial use only |
| Reign Of Kings | `reign-of-kings` | Both (Linux via Wine) | yes | no | Source RCON | ? |  |
| Renown | `renown` | Both | yes | no | Source RCON | ? |  |
| Rimworld Together - RimWorld Server | `rimworld-together` | Both | yes | no | console (STDIO) | Native |  |
| Rising Storm 2: Vietnam | `rising-storm2-vietnam` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Rising World (Unity Version) | `rising-world` | Both | yes | no | console (STDIO) | ? |  |
| Risk of Rain 2 | `risk-of-rain-2` | ? (forum: deprecated on both) | yes | yes | console (STDIO) | ? | forum list marks it deprecated (server removed from Steam) |
| Romestead | `romestead` | Both | yes | yes | console (STDIO) | ? |  |
| RuneScape: Dragonwilds | `runescape-dragonwilds` | Both | yes | yes | console (STDIO) | ? |  |
| San Andreas Multiplayer | `sa-mp` | Both | no | no | console (STDIO) | ? |  |
| Sapiens | `sapiens` | Both | yes | no | console (STDIO) | ? |  |
| Satisfactory | `satisfactory` | Both | yes | no | console (STDIO) | ? |  |
| SCP: Secret Laboratory | `scp-secret-laboratory` | Both | yes | no | console (STDIO) | ? |  |
| SCUM | `scum` | Both (Linux via Proton) | yes | yes | log file | ? |  |
| Seven Days To Die | `seven-days-to-die` | Both | yes | yes | Telnet | Emulated |  |
| Skyrim Together Reborn | `skyrim-together-reborn` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Smalland: Survive the Wilds | `smalland` | Both | no | no | console (STDIO) | No |  |
| Soldat | `soldat` | Both | no | no | console (STDIO) | ? |  |
| Sons Of The Forest | `sons-of-the-forest` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Soulmask | `soulmask` | Both | yes | no | Telnet | Emulated |  |
| Space Engineers | `space-engineers-generic` | Both (Linux via Wine) | yes | yes | console (STDIO) | ? |  |
| Space Engineers (Torch) | `space-engineers-torch` | Both (Linux via Wine) | yes | yes | console (STDIO) | ? |  |
| Spellmasons | `spellmasons` | Both | no | no | console (STDIO) | Native | template only, not on the forum list yet |
| Squad | `squad-dedicated-server` | Both | no | yes | console (STDIO) | ? |  |
| Squad 44 | `post-scriptum` | Both | no | yes | Source RCON | ? | forum list calls this Post Scriptum |
| STAR WARS Jedi Knight - Jedi Academy | `jedi-academy` | Both | yes | yes | Quake RCON | No |  |
| Starbound | `starbound` | Both | yes | yes | Source RCON | ? |  |
| Stardew Valley | `stardew-valley` | Both | no | no | console (STDIO) | ? |  |
| Starmade | `starmade` | Both | yes | yes | console (STDIO) | ? |  |
| StarRupture | `star-rupture` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Stationeers | `stationeers` | Both | yes | no | console (STDIO) | ? |  |
| Staxel | `staxel` | Both | yes | yes | console (STDIO) | ? |  |
| Stay In Tarkov | `stay-in-tarkov` | Both (inferred) | no | no | console (STDIO) | ? | non-commercial use only; template only, not on the forum list yet |
| Stormworks | `stormworks` | Both (Linux via Wine) | no | no | console (STDIO) | No |  |
| Subnautica (Legacy Nitrox Mod) | `subnautica-legacy` | Both (inferred) | no | no | console (STDIO) | No |  |
| Subnautica (Nitrox Mod) | `subnautica2` | Both | no | no | console (STDIO) | Native |  |
| Subsistence | `subsistence` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Sunkenland | `sunkenland` | Both (Linux via Wine) | no | no | log file | ? |  |
| Sven Co-op | `sven-co-op` | Both | yes | yes | GoldSrc RCON | ? |  |
| Swords 'n Magic and Stuff | `swords-n-magic-and-stuff` | Both (Linux via Wine) | no | no | console (STDIO) | ? |  |
| Synergy | `synergy` | Both | yes | no | Source RCON | No |  |
| Tarkov (Fika Mod) (Legacy) | `tarkov-fika` | Both | yes | no | console (STDIO) | Native | non-commercial use only |
| Tarkov (Fika Mod) 4.0.0+ | `tarkov-fika-new` | Both | no | no | console (STDIO) | No | non-commercial use only |
| Tarkov (Fika Mod) 4.1.0+ | `tarkov-fika-4-1` | Both | no | no | console (STDIO) | No | non-commercial use only |
| Team Fortress 2 | `team-fortress2` | Both | yes | no | Source RCON | No |  |
| Team Fortress 2 (64 Bit) | `team-fortress2-64` | Both | yes | no | Source RCON | No |  |
| Team Fortress 2 Classified | `team-fortress2-classified` | Linux only | yes | no | Source RCON | No |  |
| Team Fortress Classic | `team-fortress-classic` | Both | yes | no | GoldSrc RCON | No |  |
| Teeworlds | `teeworlds` | Both | no | no | Telnet | ? |  |
| Terraria | `terraria` | Both | yes | yes | console (STDIO) | ? |  |
| TerraTech Worlds | `terratech-worlds` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| TES3MP - The Elder Scrolls III: Morrowind Server | `tes3mp` | Both | yes | no | console (STDIO) | No |  |
| The Forest | `the-forest` | Both (Linux via Wine) | yes | yes | console (STDIO) | ? |  |
| The Front | `the-front` | Both | yes | no | console (STDIO) | ? |  |
| The Isle (EVRIMA) | `theisle-evrima` | Both | yes | no | console (STDIO) | ? |  |
| The Isle (Legacy) | `theisle-legacy` | Both (Linux via Wine) | no | yes | console (STDIO) | ? |  |
| The Lord of the Rings: Return to Moria | `lotr-moria` | Both (Linux via Wine) | yes | no | log file | ? |  |
| Titanfall 2 | `titanfall2` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| tModLoader (Legacy) | `tmodloader` | Both | yes | yes | console (STDIO) | ? |  |
| tModLoader 1.4+ | `tmodloader14` | Both | yes | yes | console (STDIO) | ? |  |
| Tower Unite | `tower-unite` | Both | no | no | console (STDIO) | ? |  |
| TShock - Terraria Server | `tshock` | Both | yes | yes | console (STDIO) | Native |  |
| Turbo Sliders Unlimited | `turbo-sliders-unlimited` | Both | no | no | console (STDIO) | Emulated |  |
| Unreal Tournament 2004 | `ut2004` | Both | no | no | console (STDIO) | ? |  |
| Unreal Tournament 99 | `ut99` | Both | no | no | console (STDIO) | ? |  |
| Unturned | `unturned` | Both | yes | yes | Telnet | ? |  |
| V Rising | `v-rising` | Both (Linux via Wine) | yes | yes | Source RCON | Emulated |  |
| Valheim | `valheim` | Both | yes | no | console (STDIO) | ? |  |
| VEIN | `vein` | Both | yes | no | console (STDIO) | ? |  |
| Veloren | `veloren` | Both | yes | no | console (STDIO) | ? |  |
| Vintage Story (Legacy) | `vintage-story` | Both | yes | yes | console (STDIO) | ? |  |
| Vintage Story 1.18.8+ | `vintage-story-new` | Both | yes | yes | console (STDIO) | Native |  |
| Voyagers of Nera | `voyagers-of-nera` | Both (Linux via Proton) | yes | yes | log file | ? |  |
| Windrose | `windrose` | Both (Linux via Wine) | yes | no | console (STDIO) | ? |  |
| Windward | `windward` | Both | yes | no | console (STDIO) | ? |  |
| Windward Horizon | `windward-horizon` | Both | yes | no | console (STDIO) | ? |  |
| Wolfenstein: Enemy Territory | `wolfenstein-et` | Both (Linux via Wine) | no | no | Quake RCON | No |  |
| Wreckfest | `wreckfest` | Both (Linux via Wine) | yes | yes | log file | ? |  |
| Wreckfest 2 | `wreckfest2` | Both (Linux via Proton) | no | no | console (STDIO) | ? |  |
| Wurm Unlimited | `wurm-unlimited` | Both | yes | yes | console (STDIO) | ? |  |
| Xonotic | `xonotic` | Both | yes | yes | console (STDIO) | ? |  |
| Zombie Panic! Source | `zombie-panic-source` | Both | yes | no | Source RCON | No |  |

## Services and tools (19)

These are not games. They run on the same Generic module.

| Service | Template | Type | OS | Join/leave | ARM | What it is |
|---|---|---|---|---|---|---|
| Bun App Runner | `bun-app-runner` | App runner | Both | no | Native |  |
| Deno App Runner | `deno-app-runner` | App runner | Both | no | Native |  |
| Dotnet App Runner | `dotnet-app-runner` | App runner | Both | no | Native |  |
| Java App Runner | `java-app-runner` | App runner | Both | no | Native |  |
| Node.js App Runner | `node` | App runner | Both | no | Native |  |
| Python App Runner | `python-app-runner` | App runner | Both | no | Native |  |
| MariaDB | `mariadb` | Database | Both | no | ? | MariaDB Service Instance |
| MongoDB | `mongodb` | Database | Both | no | Native | MongoDB Service Instance |
| MySQL | `mysql` | Database | Both | no | Native | MySQL Service Instance |
| PostgreSQL | `postgresql` | Database | Both | no | Native | PostgreSQL Service Instance |
| code-server | `code-server` | Developer tool | Linux only | no | Native | Self-hosted VS Code in the browser |
| GatekeeperV2 Bot (Deprecated) | `gatekeeperv2` | Discord bot | Both (inferred) | no | Native | AMP Discord bot by k8thekat (Gatekeeper V2); the template is marked deprecated |
| Geyser | `geyser` | Minecraft proxy | Both | yes | Native | Geyser Minecraft Proxy |
| SinusBot | `sinusbot` | Music bot | Linux only | no | No | SinusBot - TeamSpeak 3/6 and Discord music bot |
| txAdmin | `txadmin` | Server manager | Both | no | ? | txAdmin Server Manager for FiveM and RedM |
| Foundry Virtual Tabletop | `foundry-vtt` | Tabletop tool | Both | yes | Native | Foundry Virtual Tabletop Dedicated Server |
| TeamSpeak 3 | `teamspeak3` | Voice server | Both | no | ? | TeamSpeak 3 Server |
| TeamSpeak 6 | `teamspeak6` | Voice server | Both | yes | ? | TeamSpeak 6 Server |
| TeaSpeak | `teaspeak` | Voice server | Linux only | no | ? | TeaSpeak Server |

## What this means for Gatekeeper

Gatekeeper V3 has code for nine games. This is what their templates say about players and chat, so we know what a game profile in the AMP 3 edition can lean on (see `docs/DESIGN.md`, section 4).

| V3 game | Template | Join/leave | Chat | How AMP talks to it |
|---|---|---|---|---|
| Minecraft Java Edition | dedicated module | not checked | not checked | not checked |
| Minecraft Bedrock | `minecraft-bedrock` | yes | no | console (STDIO) |
| Valheim | `valheim` | yes | no | console (STDIO) |
| Terraria | `terraria` | yes | yes | console (STDIO) |
| TShock (Terraria) | `tshock` | yes | yes | console (STDIO) |
| tModLoader 1.4+ | `tmodloader14` | yes | yes | console (STDIO) |
| Factorio | `factorio` | yes | yes | Source RCON |
| Seven Days To Die | `seven-days-to-die` | yes | yes | Telnet |
| Project Zomboid | `project-zomboid` | yes | no | console (STDIO) |
| Starbound | `starbound` | yes | yes | Source RCON |
| OpenStarbound | `openstarbound` | yes | yes | Source RCON |
| Counter-Strike: Global Offensive | `counter-strike-go` | yes | no | Source RCON |
| Counter-Strike 2 | `counter-strike2` | yes | no | Source RCON |

The 55 games that define both join/leave and chat patterns can get player tracking and a chat relay with no game-specific code:

Abiotic Factor, Archean, ARK: Survival Ascended, ARK: Survival Ascended (Minimal), ARK: Survival Evolved, ARK: Survival Evolved (Minimal with Server API), ARK: Survival Evolved (Minimal), Assetto Corsa Competizione, Avorion, BeamMP, Beasts of Bermuda, Conan Exiles (Legacy), Conan Exiles Enhanced, Craftopia, DayZ (Experimental), DayZ (Stable), Don't Starve Together, Empyrion Galactic Survival, Factorio, Hytale, Insurgency Sandstorm, Just Cause 3 Multiplayer Mod, Luanti, Minetest (Legacy), Necesse, OpenStarbound, Palworld, Path of Titans, PixARK, Pumpkin, Risk of Rain 2, Romestead, RuneScape: Dragonwilds, SCUM, Seven Days To Die, Space Engineers, Space Engineers (Torch), STAR WARS Jedi Knight - Jedi Academy, Starbound, Starmade, Staxel, Sven Co-op, Terraria, The Forest, tModLoader (Legacy), tModLoader 1.4+, TShock - Terraria Server, Unturned, V Rising, Vintage Story (Legacy), Vintage Story 1.18.8+, Voyagers of Nera, Wreckfest, Wurm Unlimited, Xonotic.

Non-commercial licences matter if a community monetises a server: Farming Simulator 19, Farming Simulator 22, Farming Simulator 25, FiveM - Grand Theft Auto V Server, RAGE:MP - Grand Theft Auto V Server, RedM - Red Dead Redemption 2 Server, Stay In Tarkov, Tarkov (Fika Mod) (Legacy), Tarkov (Fika Mod) 4.0.0+, Tarkov (Fika Mod) 4.1.0+ are marked non-commercial use only in their templates.

## Gaps and caveats

- The forum list shows notes such as "Linux support via Wine" or "Docker required on Windows". This scan did not capture those notes; the "container required" notes above come from the template metadata instead.
- Minecraft Java Edition and Rust are dedicated modules, so their server types, players and chat were not checked.
- Whether AMP 3 keeps every one of these templates working is unconfirmed. Check against the beta.
- Counts will drift: the repository gets new templates and pattern fixes regularly. To refresh the numbers, read each template's `Meta.*`, `App.AdminMethod` and `Console.User*Regex` lines in `CubeCoders/AMPTemplates`.
