#ifndef MOBILE_STADIUM_H
#define MOBILE_STADIUM_H

/*
 * Structures shared by the recovered Mobile Stadium control functions.
 *
 * The names remain intentionally conservative until more of the Japanese
 * mobile code is decompiled.  Their sizes and the fields used by the matched
 * functions are known.
 */

typedef struct MobileStadiumPokemon {
    u8 unk0;
    u8 pad1[0x57];
} MobileStadiumPokemon; /* 0x58 in western builds */

typedef struct MobileStadiumTeam {
    s8 count;
    /* The resident battle-team header matches the Japanese layout: the
     * bytes after count are alignment padding, not a trainer-ID field. */
    u8 pad1[3];
    u8 name[1];
    u8 pad5[0xB];
    MobileStadiumPokemon pokemon[6];
} MobileStadiumTeam; /* 0x220 in western builds */

typedef struct MobileStadiumPokemonSource {
    u8 pad0[0x3C];
} MobileStadiumPokemonSource; /* 0x3C */

typedef struct MobileStadiumTeamSource {
    u8 pad0[0xB];
    /* Western Crystal expands the encoded nickname by five bytes. */
    u8 trainerIndex;
    u8 controllerIndex;
    u8 name[1];
    /* The western display-name field is three bytes longer as well.  The
     * party consequently begins at 0x28 instead of the Japanese 0x20. */
    u8 padE[0x1A];
    MobileStadiumPokemonSource pokemon[6];
    u8 pad188[0x4C];
} MobileStadiumTeamSource; /* 0x1DC */

typedef struct MobileStadiumTeamCopy {
    u8 name[0xC];
    u8 trainerIdHi;
    u8 trainerIdLo;
    u8 padE[2];
    MobileStadiumPokemon pokemon[6];
} MobileStadiumTeamCopy; /* 0x220 in western builds */

typedef struct MobileStadiumBattlePlayer {
    /* This is the resident battle-player descriptor, not a P3 player record.
     * The western resident routine still copies exactly 0x16 bytes. */
    u8 bytes[0x16];
} MobileStadiumBattlePlayer; /* 0x16 */

typedef struct MobileStadiumSettings {
    u8 bytes[0x1F];
} MobileStadiumSettings; /* 0x1F */

typedef struct MobileStadiumData {
    MobileStadiumTeamSource teams[2];
    u8 pad3B8[0x6C];
    s32 unk424;
    u8 pad428[0x60];
    u16 rule;
    u16 level;
    u8 pad48C[4];
} MobileStadiumData; /* 0x490 in western fragment 7 */

typedef struct MobileStadiumScreenData {
    u8 pad0[0x88];
    s16 unk88;
} MobileStadiumScreenData;

typedef struct MobileStadiumContext {
    u8 pad0[0x1B];
    u8 mode;
    u8 unk1C[4];
    MobileStadiumData* data;
    u8 pad24[8];
    MobileStadiumScreenData* screenData;
    u8 pad30[4];
    s32* battleTeams;
} MobileStadiumContext;

typedef struct MobileStadiumSelection {
    s32 data;
    s32 teams[2];
    s32 rules;
    s32 result;
    s32 selection;
    s32 value;
    MobileStadiumSettings settings;
} MobileStadiumSelection; /* 0x3C including alignment */

typedef struct MobileStadiumRules {
    u8 pad0[0x34];
    s16 rule;
    u8 pad36[0xE];
} MobileStadiumRules; /* 0x44 */

#endif
