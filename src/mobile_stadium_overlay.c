#include "global.h"
#include "mobile_stadium.h"

/*
 * Portable Mobile Stadium controller.
 *
 * The international executables retain the battle/mobile support library and
 * fragments, but replace the Japanese state-18 controller with a cleanup
 * stub.  This overlay contains only the five routines omitted from those
 * executables.  tools/mobile_stadium.py supplies the regional addresses of the
 * retained routines and fragment boundaries at link time.
 */

extern u8 D_81100000[];
extern u8 D_81400000[];
extern u8 D_81600000[];
extern u8 D_81800000[];
extern u8 D_81A00000[];
extern u8 D_82600000[];
extern u8 D_83000000[];
extern u8 D_84100000[];
extern u8 D_84200000[];
extern u8 D_84300000[];
extern u8* mobile_download_buffers[4];
extern u8 mobile_friend_counts[4];
extern u8 mobile_transfer_active_mask;
extern u8 mobile_transfer_bank_cache[4];

extern u8 fragment3_ROM_START[], fragment4_ROM_START[];
extern u8 fragment5_ROM_START[], fragment7_ROM_START[];
extern u8 fragment8_ROM_START[], fragment11_ROM_START[];
extern u8 fragment12_ROM_START[], fragment13_ROM_START[];
extern u8 fragment20_ROM_START[], fragment21_ROM_START[];
extern u8 fragment27_ROM_START[], fragment28_ROM_START[];
extern u8 fragment79_ROM_START[], fragment80_ROM_START[];
extern u8 fragment81_ROM_START[], fragment82_ROM_START[];

extern void _bzero(void*, s32);
extern void* func_80002974(u32, u32);
extern s32 func_80002B34(s32);
extern s32 func_80002BE8(s32);
extern void* func_800047D8(s32, void*, void*);
extern char* func_8000B4A0(char*, char*);
extern void func_80026858(u16);
extern s32 func_800353B4(s32, s32, s32);
extern void* mobile_finish_scan_load(s32, void*, void*);
extern void func_800354E4(s32);
extern void func_80051690(void*, void*);
extern void mobile_friend_transposer(void*, void*);
extern void func_800616AC(char*, char*);
extern void func_80065718(s16);
extern void func_80065730(s32);
extern s32 func_80065748(s32, void*, void*, s32, void*);
extern s32 func_8006585C(s32);
extern void func_8006A990(u32);
extern MobileStadiumContext* func_8006B99C(s32);
extern void func_8006BAD8(void);
extern void func_8006BB4C(void);
extern void func_8006BB58(s32);
extern s32 func_8006BB88(s32);
extern void func_8006BC18(s32);
extern u16 func_8006BC40(void);
extern void func_8006BC60(s32);
extern u32 func_8006BC84(void);
extern void func_8006BCA4(s32);
extern s16 func_8006BCEC(void);
extern void func_8006BD0C(void*);
extern s32 func_8006BD7C(u32);
extern s16 func_8006BE60(u16, u16, s32);
extern void func_8006C438(s32*);
extern s32 func_8006C488(s32);
extern s32 func_8006C4AC(s32);
extern u32 func_8006C4D0(void);
extern s32 func_8006C52C(s32);
extern s32 func_8006C570(u16, s32, s32);
extern s32 func_8006C6F0(s32);
extern s32 func_8006C714(void);
extern void func_8006CC98(s32, s32);
extern void func_8006CFFC(s32);
extern s32 func_8006D020(s32);
extern s32 func_8006D330(void);
extern s32 func_8006DDC4(void);
extern void func_8006DDE4(s32, void*, s32, s32);
extern s32 func_8006DE7C(void);
extern s32 func_8006DE9C(void);
extern s32 func_8006E208(void);
extern s32 mobile_unlock_read(s32, void*, u32, s32);
extern s32 mobile_unlock_write_crystal(s32, void*, u32, s32);

#define MOBILE_FRAGMENT_ID(vram) ((((u32)(vram) & 0x0FF00000) >> 20) - 0x10)
#define MOBILE_FRAGMENT_LOAD(vram, start, end) \
    func_800047D8(MOBILE_FRAGMENT_ID(vram), start, end)
#define MOBILE_FRAGMENT_LOAD_AND_CALL(vram, start, end, arg0, arg1) \
    func_80065748(MOBILE_FRAGMENT_ID(vram), start, end, arg0, arg1)

#define MOBILE_CRYSTAL_DOWNLOAD_OFFSET 0xF000
#define MOBILE_CRYSTAL_DOWNLOAD_SIZE 0x1000
#define MOBILE_REPLAY_STRIDE 0x490
#define MOBILE_REPLAY_REGION_SIZE 0xDB0
#define MOBILE_RECORD_BASE 0xDB4
#define MOBILE_FRIEND_RAW_OFFSET 0x1000
#define MOBILE_FRIEND_RECORD_SIZE 0x1B5
#define MOBILE_FRIEND_VALID_OFFSET 0x11BF
#define MOBILE_FRIEND_TEAM_OFFSET 0x11C0
#define MOBILE_FRIEND_TEAM_SIZE 0x220
#define MOBILE_CRYSTAL_BUFFER_SIZE 0x13E0

static s32 mobile_validate_p3(u8* data, s32 length, u8* footer) {
    u32 sum = 0;
    s32 i;

    for (i = 0; i < length; i++) {
        sum = (sum + data[i]) & 0xFFFF;
    }
    return footer[0] == 'P' && footer[1] == '3' &&
           sum == (footer[2] | (footer[3] << 8));
}

static void mobile_copy_bytes(u8* dst, u8* src, s32 length) {
    s32 i;

    for (i = 0; i < length; i++) {
        dst[i] = src[i];
    }
}

/*
 * The retained Transfer Pak driver caches the selected Game Boy SRAM bank in
 * N64 RAM.  On real hardware the physical MBC30 mapper may have been reset or
 * changed independently (for example while switching scanner/Lab/Mobile
 * operations), leaving that cache stale.  Force the next resident read/write
 * to issue the bank-select command.  N-Rage's linear SRAM model does not
 * expose this failure, which is why the old code worked in emulation only.
 */
static void mobile_invalidate_mapper_bank(s32 controller) {
    if (controller >= 0 && controller < 4) {
        mobile_transfer_bank_cache[controller] = 0xFF;
    }
}

static s32 mobile_hardware_read(s32 controller, void* data, u32 offset,
                                s32 length) {
    mobile_invalidate_mapper_bank(controller);
    return mobile_unlock_read(controller, data, offset, length);
}

/* The retained driver already divides a logical read into physical Transfer
 * Pak blocks and selects offset >> 13 as the SRAM bank. Retail Japanese
 * Stadium passes E000/F000 reads to it whole. Re-selecting bank 7 before
 * every 0x20-byte block is both redundant and hostile to some MBC30 flash
 * carts, so fallback attempts repeat the complete retail-style request. */
static s32 mobile_hardware_read_blocks(s32 controller, u8* data, u32 offset,
                                       s32 length) {
    return mobile_hardware_read(controller, data, offset, length);
}

static s32 mobile_hardware_write(s32 controller, void* data, u32 offset,
                                 s32 length) {
    mobile_invalidate_mapper_bank(controller);
    return mobile_unlock_write_crystal(controller, data, offset, length);
}

static s32 mobile_p3_frame_valid(u8* data) {
    u8* record;
    s32 i;

    if (data[0] > 3 || data[1] > 5 ||
        !mobile_validate_p3(data, 0xFFC, data + 0xFFA)) {
        return 0;
    }
    /* Commands 0x27 and 0x26 consume independently checksummed wire
     * records.  Reject a frame whose advertised entries would make either
     * command hand Fragment 7 corrupt western data. */
    for (i = 0; i < data[0]; i++) {
        record = data + 4 + i * MOBILE_REPLAY_STRIDE;
        if (!mobile_validate_p3(record, 0x48E, record + 0x48C)) {
            return 0;
        }
    }
    for (i = 0; i < data[1]; i++) {
        record = data + MOBILE_RECORD_BASE + i * 0x48;
        if (!mobile_validate_p3(record, 0x46, record + 0x44)) {
            return 0;
        }
    }
    return 1;
}

static s32 mobile_friend_record_valid(u8* data) {
    u32 sum = 0;
    s32 i;

    for (i = 0; i < 0x1B3; i++) {
        sum = (sum + data[i]) & 0xFFFF;
    }
    return data[0x1B1] == 'P' && data[0x1B2] == '3' &&
           sum == (data[0x1B3] | (data[0x1B4] << 8));
}

/* Crystal's peer-to-peer record is not a Stadium team object.  It stores the
 * opponent name, six nickname strings, six OT strings, and six 0x30-byte
 * party_struct objects as separate arrays.  Fragment 7 expects command 0x28
 * to return a converted 0x220-byte western team (six 0x58-byte Pokemon).
 * The Japanese command handler performed this transposition before returning
 * the record; international builds removed that handler entirely. */
static void mobile_convert_friend_team(u8* dst, u8* raw) {
    u8 source[0x3C];
    u8* party;
    u8* nickname;
    u8* converted;
    s32 i;

    _bzero(dst, MOBILE_FRIEND_TEAM_SIZE);
    /* Command 0x28 returns Fragment 7's selection record, not the final
     * battle-team record. Its encoded trainer name begins at +0 and its
     * converted party begins at +0x10. */
    func_800616AC((char*)dst, (char*)raw);
    /* FriendDataStructure.txt stores the opponent's big-endian TID directly
     * at raw +0x0b/+0x0c. Fragment 7's western selection record has a
     * 12-byte encoded-name field and consumes the TID at +0x0c/+0x0d. */
    dst[0x0C] = raw[0x0B];
    dst[0x0D] = raw[0x0C];
    for (i = 0; i < 6; i++) {
        party = raw + 0x91 + i * 0x30;
        nickname = raw + 0x0D + i * 0x0B;
        _bzero(source, sizeof(source));
        /* Kept resident in the unused rmonPrintf body.  Fragment growth here
         * corrupts the dormant international fragment loader before entry. */
        mobile_friend_transposer(source, party);
        mobile_copy_bytes(source + 0x24, nickname, 0x0B);
        source[0x2F] = 0x50;
        mobile_copy_bytes(source + 0x30, raw + 0x4F + i * 0x0B, 0x0B);
        source[0x3B] = 0x50;
        converted = dst + 0x10 + i * 0x58;
        func_80051690(converted, source);
    }
}

static void mobile_read_friend(s32 controller, u8* data) {
    u8* raw = data + MOBILE_FRIEND_RAW_OFFSET;
    u8* team = data + MOBILE_FRIEND_TEAM_OFFSET;
    s32 retry;

    /* Command 0x28 has a separate retained count query.  Unlike commands
     * 0x26 and 0x27, it does not read the count from DLD0: func_8005CDDC
     * reads this one-byte-per-controller array instead.  Keep it in sync
     * with the native western record copied below or Fragment 7 takes its
     * normal empty-list path without ever invoking mobile_download_copy. */
    mobile_friend_counts[controller] = 0;
    data[MOBILE_FRIEND_VALID_OFFSET] = 0;
    /* Read an aligned block beginning with sMobileStadiumFlag.  The native
     * western Friend Data object itself begins one byte later at raw 0xe001. */
    /* Preserve the known-good full request used by N-Rage.  If its checksum
     * is incomplete on a physical MBC30 cart, retry as real 0x20-byte Pak
     * blocks with an explicit mapper-bank selection for every block. */
    mobile_hardware_read(controller, raw, 0xE000, 0x1C0);
    if (mobile_friend_record_valid(raw + 1)) {
        mobile_convert_friend_team(team, raw + 1);
        data[MOBILE_FRIEND_VALID_OFFSET] = 1;
        mobile_friend_counts[controller] = 1;
        return;
    }
    for (retry = 0; retry < 3; retry++) {
        _bzero(raw, 0x1C0);
        if (mobile_hardware_read_blocks(controller, raw, 0xE000, 0x1C0) != 0) {
            continue;
        }
        if (mobile_friend_record_valid(raw + 1)) {
            mobile_convert_friend_team(team, raw + 1);
            data[MOBILE_FRIEND_VALID_OFFSET] = 1;
            mobile_friend_counts[controller] = 1;
            return;
        }
    }
    _bzero(team, MOBILE_FRIEND_TEAM_SIZE);
}

static s32 mobile_read_p3(s32 controller, u8* data) {
    s32 retry;

    /* Keep the established emulator path, then fall back to physical Pak
     * blocks only if the P3 frame proves the full request was incomplete. */
    mobile_hardware_read(controller, data,
                         MOBILE_CRYSTAL_DOWNLOAD_OFFSET,
                         MOBILE_CRYSTAL_DOWNLOAD_SIZE);
    if (mobile_p3_frame_valid(data)) {
        return 1;
    }
    for (retry = 0; retry < 3; retry++) {
        _bzero(data, MOBILE_CRYSTAL_DOWNLOAD_SIZE);
        if (mobile_hardware_read_blocks(controller, data,
                                        MOBILE_CRYSTAL_DOWNLOAD_OFFSET,
                                        MOBILE_CRYSTAL_DOWNLOAD_SIZE) != 0) {
            continue;
        }
        if (mobile_p3_frame_valid(data)) {
            return 1;
        }
    }
    return mobile_p3_frame_valid(data);
}

/*
 * The Japanese game performs this reverse handshake from Mobile Stadium's
 * controller, not from the shared cartridge scanner. Keeping it here avoids
 * changing the Transfer Pak result state after an unrelated Mystery Gift.
 */
static void mobile_enable_crystal(s32 controller) {
    u8 status1[0x20];
    u8 status2[0x20];
    u8 flags[0x20];

    if (mobile_hardware_read(controller, status1, 0x8B00, 0x20) != 0 ||
        mobile_hardware_read(controller, status2, 0xE780, 0x20) != 0 ||
        (status1[0x10] ^ status2[0x1A]) != 0xFF || status1[0x10] == 0xFF) {
        return;
    }
    if (mobile_hardware_read(controller, flags, 0xE000, 0x20) == 0 &&
        flags[0] != 1) {
        flags[0] = 1;
        mobile_hardware_write(controller, flags, 0xE000, 0x20);
    }
}

/*
 * Commands 0x26-0x28 were left as success-only jump-table entries in the
 * international executable. Commands 0x26 and 0x27 return native western
 * download objects. Command 0x28 returns the western 0x220-byte team made
 * from Crystal's separate peer-to-peer arrays by mobile_convert_friend_team.
 */
s32 mobile_download_copy(void* destination, s32 controller, s32 index,
                         s32 command) {
    u8* data;
    u8* dst = destination;

    if (destination == NULL || controller < 0 || controller >= 4 || index < 0) {
        return 0;
    }
    data = mobile_download_buffers[controller];
    if (data == NULL) {
        return 0;
    }
    if (command == 0x26) {
        if (index >= data[1]) {
            return 0;
        }
        /* Command 0x26's destination is a 0x44-byte rule body.  The trailing
         * P3 marker/checksum belongs to the download framing, not the object. */
        mobile_copy_bytes(dst, data + MOBILE_RECORD_BASE + index * 0x48,
                          0x44);
        return 1;
    }
    if (command == 0x28) {
        if (index != 0 || data[MOBILE_FRIEND_VALID_OFFSET] == 0) {
            return 0;
        }
        mobile_copy_bytes(dst, data + MOBILE_FRIEND_TEAM_OFFSET,
                          MOBILE_FRIEND_TEAM_SIZE);
        return 1;
    }
    if (command != 0x27 || index >= data[0]) {
        return 0;
    }
    mobile_copy_bytes(dst, data + 4 + index * MOBILE_REPLAY_STRIDE,
                      MOBILE_REPLAY_STRIDE);
    return 1;
}
/*
 * Restore the Japanese DLD0 loader omitted from every international build.
 * The service payload remains a fixed 0x1000-byte P3 object. Western Crystal
 * stores three native 0x490-byte replay slots followed by five 0x48-byte
 * organizer records.
 *
 * Keep an allocated, zero-count buffer even when a cart has no valid payload.
 * The retained command dispatcher unconditionally dereferences this table
 * while drawing the Game Pak picker, which is the null access that caused the
 * selection soft crash in international ROMs.
 */
void mobile_load_download(s32 controller) {
    u8* data;
    u8* raw;
    u8* team;

    /* The resident state wrapper performs physical E000/F000 reads before it
     * maps this overlay.  A non-NULL buffer is therefore a prefetched raw
     * image, not merely an already-finalized object.  Validate and transpose
     * it here, after every Pak has been released and the overlay TLB mapping
     * is stable. */
    if (mobile_download_buffers[controller] != NULL) {
        data = mobile_download_buffers[controller];
        if (!mobile_p3_frame_valid(data)) {
            /* Do not leave an erased or partially initialized Crystal
             * download frame visible to the retained count dispatcher.  It
             * does not validate the P3 footer and would otherwise interpret
             * FF/FF as advertised Battle/Rule entries. */
            _bzero(data, MOBILE_CRYSTAL_DOWNLOAD_SIZE);
        }
        raw = data + MOBILE_FRIEND_RAW_OFFSET;
        team = data + MOBILE_FRIEND_TEAM_OFFSET;
        mobile_friend_counts[controller] = 0;
        data[MOBILE_FRIEND_VALID_OFFSET] = 0;
        if (mobile_friend_record_valid(raw + 1)) {
            mobile_convert_friend_team(team, raw + 1);
            data[MOBILE_FRIEND_VALID_OFFSET] = 1;
            mobile_friend_counts[controller] = 1;
        } else {
            _bzero(team, MOBILE_FRIEND_TEAM_SIZE);
        }
        return;
    }
    /* A missing resident buffer means that this controller did not complete
     * the scanner-owned physical session.  Never allocate or touch the Pak
     * here: both operations occur after fragment 53 has been mapped and can
     * invalidate the 0x84500000 execution window. */
}

void mobile_load_downloads(void) {
    s32 controller;

    for (controller = 0; controller < 4; controller++) {
        /* This routine only validates/transposes resident-prefetched bytes.
         * mobile_load_download() has no allocation or physical Pak fallback. */
        mobile_load_download(controller);
    }
}
static void mobile_copy_source_teams(MobileStadiumTeam* dst,
                                     MobileStadiumTeamSource* src) {
    s32 i;
    s32 j;

    for (i = 0; i < 2; i++) {
        func_800616AC(dst[i].name, (char*)&src[i]);
        dst[i].count = 0;
        for (j = 0; j < 6; j++) {
            func_80051690(&dst[i].pokemon[j], &src[i].pokemon[j]);
            if (dst[i].pokemon[j].unk0 != 0) {
                dst[i].count++;
            }
        }
    }
}

static void mobile_copy_selected_teams(MobileStadiumTeam* dst,
                                       MobileStadiumTeamCopy** src) {
    s32 i;
    s32 j;

    for (i = 0; i < 2; i++) {
        func_8000B4A0((char*)dst[i].name, (char*)src[i]->name);
        /* The ID in MobileStadiumTeamCopy is used by Fragment 7's preview.
         * The resident battle-team header has padding at +1..+3; the battle
         * engine obtains each trainer ID from the converted Pokemon data. */
        dst[i].count = 0;
        for (j = 0; j < 6; j++) {
            /* IDO's whole-structure assignment can lower this 0x58-byte
             * object through an ABI helper on some regional builds.  Copy
             * explicitly, matching the Japanese routine's field-for-field
             * behavior while retaining the western stride. */
            u8* pokemon = (u8*)&src[i]->pokemon[j];

            mobile_copy_bytes((u8*)&dst[i].pokemon[j], pokemon,
                              sizeof(MobileStadiumPokemon));
            if (dst[i].pokemon[j].unk0 != 0) {
                dst[i].count++;
            }
        }
    }
}

static void mobile_make_selection(MobileStadiumSelection* dst,
                                  MobileStadiumContext* context,
                                  MobileStadiumTeamCopy** teams,
                                  MobileStadiumRules* rules,
                                  MobileStadiumSettings* settings) {
    s32 i;

    if (dst == NULL) {
        return;
    }
    dst->data = (s32)context->data;
    for (i = 0; i < 2; i++) {
        dst->teams[i] = (s32)teams[i];
    }
    dst->rules = (s32)rules;
    dst->settings = *settings;
}

static s32 mobile_choose_battle(s32 state, MobileStadiumContext* context,
                                MobileStadiumBattlePlayer* players,
                                s32* trainers, s32* controllers,
                                MobileStadiumSettings* settings) {
    s32 result;
    s32 i;
    u16 rule;
    MobileStadiumTeamSource* source;
    s32 choice;
    s32 mode = 0;
    MobileStadiumRules* rules;
    MobileStadiumSelection* selection;
    MobileStadiumTeamCopy* teams[2];
    /* Keep the international engine's proven participant-state initializer.
     * The Japanese controller's sentinel array belongs to its different
     * resident battle engine and deadlocks the retained western path. */
    s32 battle[4] = { 0 };

    /* Finish the resident-prefetched E000/F000 data before calling the
     * retained western allocators.  PAL's allocator path can invalidate the
     * mapped 0x84500000 fragment; calling mobile_load_downloads() afterwards
     * therefore faults on its first instruction even though the equivalent
     * NTSC instruction schedule happens to survive.  This work performs no
     * physical Pak I/O and is safe while the controller overlay is mapped. */
    mobile_load_downloads();
    rules = func_80002974(0x44, 0);
    selection = func_80002974(0x3C, 0);

    for (i = 0; i < 2; i++) {
        teams[i] = func_80002974(sizeof(MobileStadiumTeamCopy), 0);
        if (teams[i] != NULL) {
            _bzero(teams[i], sizeof(MobileStadiumTeamCopy));
        }
    }
    /* The resident state wrapper has already completed the ordinary scanner
     * pass and reacquired each usable Transfer Pak before mapping this
     * overlay.  Never scan or acquire from 0x84500000: either operation can
     * replace this fragment's TLB mapping on real hardware and Project64. */
    /* Finalize DLD0-DLD3 from the raw E000/F000 bytes that the resident
     * wrapper copied before it released the physical Transfer Pak.  No Pak
     * session remains live while this overlay or Fragment 7 is mapped. */
    mobile_finish_scan_load(MOBILE_FRAGMENT_ID(D_81A00000),
                            fragment3_ROM_START, fragment4_ROM_START);
    MOBILE_FRAGMENT_LOAD(D_81600000, fragment11_ROM_START, fragment12_ROM_START);
    MOBILE_FRAGMENT_LOAD(D_81800000, fragment4_ROM_START, fragment5_ROM_START);
    mobile_make_selection(selection, context, teams, rules, settings);
    MOBILE_FRAGMENT_LOAD_AND_CALL(D_83000000, fragment7_ROM_START,
                                  fragment8_ROM_START, 0, selection);
    *settings = selection->settings;
    choice = 0;
    switch (selection->result) {
        default:
        case 0:
            result = 0;
            break;
        case 1:
            result = 4;
            choice = 1;
            mode = 2;
            battle[1] = 1;
            break;
        case 2:
            result = 4;
            choice = 2;
            battle[1] = -1;
            break;
        case 3:
            result = 4;
            choice = 3;
            battle[1] = -1;
            break;
    }
    if (result != 0) {
        func_8006C438(battle);
        func_8006CFFC(mode);
        *context->battleTeams = func_8006E208();
        switch (choice) {
            case 1:
            case 2:
                for (i = 0; i < 2; i++) {
                    source = &context->data->teams[i];
                    func_8000B4A0((char*)&players[i], (char*)source->name);
                    trainers[i] = source->trainerIndex;
                    controllers[i] = source->controllerIndex;
                }
                rule = context->data->rule & 0xFF;
                func_8006BC18(rule | 0x700);
                func_8006BC60(context->data->level);
                if (rule != 0x10) {
                    func_8006BCA4(func_8006BE60(context->data->rule,
                                               context->data->level, 0));
                } else {
                    func_8006BD0C(&context->data->unk424);
                }
                mobile_copy_source_teams(
                    (MobileStadiumTeam*)*context->battleTeams,
                    context->data->teams);
                break;
            case 3:
                for (i = 0; i < 2; i++) {
                    trainers[i] = 0x40;
                    if (i != 0) {
                        controllers[i] = 1;
                    } else {
                        /* The resident battle screens use this byte as the
                         * Crystal trainer-portrait variant.  Fragment 7's
                         * local selection value is already 0 for male and 1
                         * for female; inverting it made only the later party,
                         * VS and result screens show the wrong portrait. */
                        controllers[i] = selection->selection;
                    }
                }
                func_8006A990(rules->rule);
                func_80065718(0);
                func_80065730(selection->value);
                if (rules->rule != 0xE) {
                    func_8006BCA4(rules->rule);
                } else {
                    func_8006BD0C(rules);
                }
                mobile_copy_selected_teams(
                    (MobileStadiumTeam*)*context->battleTeams, teams);
                break;
        }
        func_8006D020(choice);
        func_8006CC98(*context->battleTeams, 1);
        *context->battleTeams = 0;
    }
    return result;
}

void mobile_stadium_entry(void) {
    s32 i;
    MobileStadiumContext* context;
    s32 value;
    s32 state = 11;
    MobileStadiumBattlePlayer players[2];
    s32 trainers[2];
    s32 controllers[2];
    MobileStadiumSettings settings;

    _bzero(&settings, 0x1F);
    func_80002B34('btpc');
    context = func_8006B99C(0);
    /* The retained western allocator already reserves and clears 0x490 bytes
     * for context->data. */
    func_8006BC18(2);
    func_8006BAD8();
    func_8006C488(0);
    func_8006C52C(1);
    while (state != 0) {
        func_80002B34('btlp');
        switch (state) {
            case 7:
            case 9:
            case 10:
                break;
            case 11:
                for (i = 0; i < 2; i++) {
                    _bzero(&players[i], sizeof(MobileStadiumBattlePlayer));
                    controllers[i] = trainers[i] = -1;
                }
                state = mobile_choose_battle(state, context, players, trainers,
                                             controllers, &settings);
                break;
            case 4:
                func_800353B4(9, 0, 0);
                if (func_8006DDC4() != 0) {
                    for (i = 0; i < 2; i++) {
                        func_8006DDE4(i, &players[i], trainers[i], controllers[i]);
                    }
                    MOBILE_FRAGMENT_LOAD(D_84300000, fragment81_ROM_START,
                                         fragment82_ROM_START);
                    MOBILE_FRAGMENT_LOAD_AND_CALL(
                        D_82600000, fragment20_ROM_START, fragment21_ROM_START,
                        1, context->screenData);
                    func_8006C4AC(context->screenData->unk88);
                    if (context->mode != 3) {
                        func_8006C6F0(func_8006C570(context->data->rule,
                                                   context->data->level,
                                                   func_8006C4D0()));
                    } else {
                        func_8006C6F0(func_8006C570(
                            func_8006BC40(), func_8006BC84(), func_8006C4D0()));
                    }
                }
                state = 5;
                break;
            case 5:
                value = func_8006D330();
                if (value != 0) {
                    MOBILE_FRAGMENT_LOAD(D_84200000, fragment80_ROM_START,
                                         fragment81_ROM_START);
                    MOBILE_FRAGMENT_LOAD(D_81600000, fragment11_ROM_START,
                                         fragment12_ROM_START);
                    MOBILE_FRAGMENT_LOAD_AND_CALL(
                        D_81400000, fragment12_ROM_START, fragment13_ROM_START,
                        0, (void*)value);
                    state = 6;
                }
                break;
            case 6:
                func_80026858(func_8006C714());
                func_800353B4(8, func_8006BD7C(func_8006BCEC()), 0xB);
                func_800354E4(0x5A);
                MOBILE_FRAGMENT_LOAD(D_81100000, fragment27_ROM_START,
                                     fragment28_ROM_START);
                if (MOBILE_FRAGMENT_LOAD_AND_CALL(
                        D_84100000, fragment79_ROM_START, fragment80_ROM_START,
                        func_8006C714(), (void*)func_8006DE9C()) == 1) {
                    state = 11;
                } else {
                    state = 8;
                }
                break;
            case 8:
                func_800353B4(0xA, 0, 0);
                if (func_8006DE7C() != 0) {
                    for (i = 0; i < 2; i++) {
                        func_8006DDE4(i, &players[i], trainers[i], controllers[i]);
                    }
                    MOBILE_FRAGMENT_LOAD_AND_CALL(
                        D_82600000, fragment20_ROM_START, fragment21_ROM_START,
                        2, context->screenData);
                    state = 11;
                }
                break;
        }
        func_80002BE8('btlp');
    }
    func_8006BB4C();
    func_80002BE8('btpc');
    func_8006585C(2);
}
