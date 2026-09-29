.set noat
.set noreorder

.text
.globl mobile_download_copy_trampoline
.ent mobile_download_copy_trampoline
mobile_download_copy_trampoline:
    # func_80062390 retains the advancing destination in s2.  The unused
    # fifth argument slot is not stable on this international call path.
    or      $a0, $s2, $zero
    lw      $a1, 0x24($sp)
    lw      $a2, 0x2C($sp)
    lw      $a3, 0x20($sp)
    jal     mobile_download_copy
     nop
    or      $v1, $v0, $zero
    j       mobile_download_copy_continue
     nop
.end mobile_download_copy_trampoline
