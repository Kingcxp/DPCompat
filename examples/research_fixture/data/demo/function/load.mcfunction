tellraw @s {"text":"Open","clickEvent":{"action":"run_command","value":"/say migrated"}}
summon minecraft:zombie ~ ~ ~ {FallDistance:1.0f,ArmorItems:[{},{},{},{id:"minecraft:diamond_helmet",count:1}]}
item replace entity @s horse.saddle with minecraft:saddle
give @s minecraft:chain
spawnpoint @s ~ ~ ~ 90
gamerule doDaylightCycle false
gamerule disableRaids true
