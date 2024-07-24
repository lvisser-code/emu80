"""Emulator for the Intel 8080A microprocessor

Author: Leonard Visser

Reads hex file, creates an emulated processor, memory, IO system.  Code
can be executed and when breakpoint or Halt is encountered the state of
the machine can be displayed.

Commands
  B Addr        ;Set breakpoint address
  C             ;Clear breakpoint
  D Addr (Addr) ;Display memory range
  E Addr        ;Execute from address
  F flag bit    ;Set flags (C, V, P, AC, K, S, Z)
  H(elp)        ;Display help
  L name.hex    ;Load hex file
  M Addr byte (byte) ;Set memory
  P Port (byte) ;Display/Set I/O port
  Q(uit)        ;Quit
  R             ;Display/set registers (A, BC, DE, HL, PSW, PC, SP)
  S (Addr)      ;Single step from address or current PC

Hooks for hardware
  OUT 2 will display a character from regs['A']
  IN  3 will return A=1
  CALL to 0020H will be redirected to input a line of text from keyboard
  JZ to 0023H will be redirected to save the program from simulated memory to disk
  JZ to 0026H will be redirected to load a program from disk to simulated memory
  columns is set equal to the number of columns in the display
"""
import sys

breakpoint = -1
single_step = 0
error = -1
periods = 0
column = 1
columns = 80
memory = [0] * 2**16
port = [0] * 256
invalid = False
regs = {"A":0, "B":0, "C":0, "D":0, "E":0, "H":0, "L":0, "PC":0, "SP":0, "RIM":0, "SIM":0}
flags = {"S":0, "Z":0, "K":0, "AC":0, "P":0, "V":0, "CY":0}
save_program = ""
save_flag = 0
fname = ''
fload = False
fsize = 0
fline = 0

def set_flags_ZSP( n ): # Set zero, sign, parity flags
    global flags
    if n == 0:
        flags['Z'] = 1
    else:
        flags['Z'] = 0
    if n > 127:
        flags['S'] = 1
    else:
        flags['S'] = 0
    parity = 1
    while n:
        parity *= -1
        n = n & (n - 1)
    if parity == -1: parity = 0
    flags['P'] = parity

def instruction_00(): # NOP
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'NOP')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_01(): # LXI B,D16
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'LXI B,' + str.format('{:02X}', memory[regs['PC']+2]) \
        + str.format('{:02X}', memory[regs['PC']+1]))
    regs['C'] = memory[regs['PC']+1]
    regs['B'] = memory[regs['PC']+2]
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_02(): # STAX B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'STAX B')
    memory[256*regs['B'] + regs['C']] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_03(): # INX B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INX B')
    bc = (256*regs['B'] + regs['C'] + 1)
    if bc > 65535:
        bc = 0
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['B'] = bc // 256
    regs['C'] = bc % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_04(): # INR B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR B')
    i = regs['B'] + 1
    if i > 255:
        i = 0
    regs['B'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_05(): # DCR B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR B')
    i = regs['B'] - 1
    if i < 0:
        i = 255
    regs['B'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_06(): # MVI B,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI B,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['B'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_07(): # RLC
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'RLC')
    i = regs['A'] << 1
    regs['A'] = (i & 255) + (i // 256)
    flags['CY'] = i // 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_08(): # undefined
    print ('Undefined instuction 08 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_09(): # DAD B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DAD B')
    hl = 256*regs['H'] + regs['L']
    bc = 256*regs['B'] + regs['C']
    i = hl + bc
    flags['CY'] = i // 65536
    i = i & 65535
    regs['H'] = i // 256
    regs['L'] = i % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_0A(): # LDAX B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'LDAX B')
    regs['A'] = memory[256*regs['B'] + regs['C']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_0B(): # DCX B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCX B')
    bc = (256*regs['B'] + regs['C'] - 1)
    if bc < 0:
        bc = 65535
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['B'] = bc // 256
    regs['C'] = bc % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_0C(): # INR C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR C')
    i = regs['C'] + 1
    if i > 255:
        i = 0
    regs['C'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_0D(): # DCR C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR C')
    i = (regs['C'] - 1)
    if i < 0:
        i = 255
    regs['C'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_0E(): # MVI C,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI C,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['C'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_0F(): # RRC
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'RRC')
    flags['CY'] = regs['A'] & 1
    regs['A'] = (regs['A'] >> 1) + (128 * flags['CY'])
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_10(): # ARHL (undocumented)
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ARHL')
    flags['CY'] = regs['L'] & 1
    regs['L'] = (regs['L'] >> 1) + 128*(regs['H'] & 1)
    regs['H'] = (regs['H'] >> 1) + (regs['H'] & 128)
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_11(): # LXI D,D16
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'LXI D,' + str.format('{:02X}', memory[regs['PC']+2]) \
        + str.format('{:02X}', memory[regs['PC']+1]))
    regs['E'] = memory[regs['PC']+1]
    regs['D'] = memory[regs['PC']+2]
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_12(): # STAX D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'STAX D')
    memory[256*regs['D'] + regs['E']] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_13(): # INX D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INX D')
    de = (256*regs['D'] + regs['E'] + 1)
    if de > 65535:
        de = 0
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['D'] = de // 256
    regs['E'] = de % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_14(): # INR D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR D')
    i = regs['D'] + 1
    if i > 255:
        i = 0
    regs['D'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_15(): # DCR D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR D')
    i = (regs['D'] - 1)
    if i < 0:
        i = 255
    regs['D'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_16(): # MVI D,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI D,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['D'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_17(): # RAL
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'RAL')
    i = regs['A'] << 1
    regs['A'] = (i & 255) + flags['CY']
    flags['CY'] = i//256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_18(): # undefined
    print ('Undefined instuction 18 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_19(): # DAD D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DAD D')
    hl = 256*regs['H'] + regs['L']
    de = 256*regs['D'] + regs['E']
    i = hl + de
    flags['CY'] = i // 65536
    i = i & 65535
    regs['H'] = i // 256
    regs['L'] = i % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_1A(): # LDAX D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'LDAX D')
    regs['A'] = memory[256*regs['D'] + regs['E']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_1B(): # DCX D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCX D')
    de = (256*regs['D'] + regs['E'] - 1)
    if de < 0:
        de = 65535
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['D'] = de // 256
    regs['E'] = de % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_1C(): # INR E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR E')
    i = regs['E'] + 1
    if i > 255:
        i = 0
    regs['E'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_1D(): # DCR E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR E')
    i = (regs['E'] - 1)
    if i < 0:
        i = 255
    regs['E'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_1E(): # MVI E,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI E,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['E'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_1F(): # RAR
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'RAR')
    i = flags['CY']
    flags['CY'] = regs['A'] % 2
    regs['A'] = (regs['A'] >> 1) + (i * 128)
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_20(): # undefined
    print ('Undefined instuction 20 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_21(): # LXI H,D16
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'LXI H,' + str.format('{:02X}', memory[regs['PC']+2]) \
        + str.format('{:02X}', memory[regs['PC']+1]))
    regs['L'] = memory[regs['PC']+1]
    regs['H'] = memory[regs['PC']+2]
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_22(): # SHLD Adr
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SHLD ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    i = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    memory[i] = regs['L']
    memory[i+1] = regs['H']
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 16

def instruction_23(): # INX H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INX H')
    hl = (256*regs['H'] + regs['L'] + 1)
    if hl > 65535:
        hl = 0
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['H'] = hl // 256
    regs['L'] = hl % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_24(): # INR H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR H')
    i = regs['H'] + 1
    if i > 255:
        i = 0
    regs['H'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_25(): # DCR H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR H')
    i = regs['H'] - 1
    if i < 0:
        i = 255
    regs['H'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_26(): # MVI H,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI H,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['H'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_27(): # DAA
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DAA')
    ln = regs['A'] & 15
    if ln > 9 or flags['AC'] == 1:
        regs['A'] += 6
    if ln > 9:
        flags['AC'] = 1
    hn = regs['A'] // 16
    if hn > 9 or flags['CY'] == 1:
        regs['A'] = (regs['A'] + 96) % 256
    if hn > 9:
        flags['CY'] = 1
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_28(): # undefined
    print ('Undefined instuction 28 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_29(): # DAD H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DAD H')
    i = 2*(256*regs['H'] + regs['L'])
    flags['CY'] = i // 2**16
    i = i & 65535
    regs['H'] = i // 256
    regs['L'] = i % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_2A(): # LHLD Adr
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'LHLD ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    i = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    regs['L'] = memory[i]
    regs['H'] = memory[i+1]
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 16

def instruction_2B(): # DCX H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCX H')
    hl = (256*regs['H'] + regs['L'] - 1)
    if hl < 0:
        hl = 65535
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['H'] = hl // 256
    regs['L'] = hl % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_2C(): # INR L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR L')
    i = regs['L'] + 1
    if i > 255:
        i = 0
    regs['L'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_2D(): # DCR L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR L')
    i = regs['L'] - 1
    if i < 0:
        i = 255
    regs['L'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_2E(): # MVI L,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI L,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['L'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_2F(): # CMA
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMA')
    regs['A'] = (~ regs['A']) & 255
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_30(): # undefined
    print ('Undefined instuction 30 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True


def instruction_31(): # LXI SP,D16
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'LXI SP,' + str.format('{:02X}', memory[regs['PC']+2]) \
        + str.format('{:02X}', memory[regs['PC']+1]))
    regs['SP'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_32(): # STA Adr
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'STA ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    a = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    memory[a] = regs['A']
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 13

def instruction_33(): # INX SP
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INX SP')
    sp = (regs['SP'] + 1)
    if sp > 65535:
        sp = 0
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_34(): # INR M
    global memory, periods, regs, flags

    if single_step: print(str.format('{:04X}', regs['PC']),'INR M')
    i = memory[256*regs['H'] + regs['L']] + 1
    if i > 255:
        i = 0
    memory[256*regs['H'] + regs['L']] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_35(): # DCR M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR M')
    i = memory[256*regs['H'] + regs['L']] - 1
    if i < 0:
        i = 255
    memory[256*regs['H'] + regs['L']] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_36(): # MVI M,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI M,' + str.format('{:02X}', memory[regs['PC']+1]))
    memory[256*regs['H'] + regs['L']] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 10

def instruction_37(): # STC
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'STC')
    flags['CY'] = 1
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_38(): # undefined
    print ('Undefined instuction 38 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_39(): # DAD SP
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DAD SP')
    i = 256*regs['H'] + regs['L'] + regs['SP']
    flags['CY'] = i // 2**16
    i = i & 65535
    regs['H'] = i // 256
    regs['L'] = i % 256
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_3A(): # LDA Adr
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'LDA ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    a = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    regs['A'] = memory[a]
    regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 13

def instruction_3B(): # DCX SP
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCX SP')
    sp = (regs['SP'] - 1)
    if sp < 0:
        sp = 65535
        flags['K'] = 1
    else:
        flags['K'] = 0
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_3C(): # INR A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'INR A')
    i = regs['A'] + 1
    if i > 255:
        i = 0
    regs['A'] = i
    set_flags_ZSP(i)
    if i & 15 == 0:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_3D(): # DCR A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'DCR A')
    i = regs['A'] - 1
    if i < 0:
        i = 255
    regs['A'] = i
    set_flags_ZSP(i)
    if i & 15 == 15:
        flags['AC'] = 1
    else:
        flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_3E(): # MVI A,D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
        'MVI A,' + str.format('{:02X}', memory[regs['PC']+1]))
    regs['A'] = memory[regs['PC']+1]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_3F(): # CMC
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMC')
    if (flags['CY'] == 0):
        flags['CY'] = 1
    else:
        flags['CY'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_40(): # MOV B,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,B')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_41(): # MOV B,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,C')
    regs['B'] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_42(): # MOV B,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,D')
    regs['B'] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_43(): # MOV B,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,E')
    regs['B'] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_44(): # MOV B,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,H')
    regs['B'] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_45(): # MOV B,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,L')
    regs['B'] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_46(): # MOV B,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,M')
    regs['B'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_47(): # MOV B,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV B,A')
    regs['B'] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_48(): # MOV C,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,B')
    regs['C'] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_49(): # MOV C,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,C')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_4A(): # MOV C,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,D')
    regs['C'] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_4B(): # MOV C,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,E')
    regs['C'] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_4C(): # MOV C,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,H')
    regs['C'] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_4D(): # MOV C,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,L')
    regs['C'] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_4E(): # MOV C,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,M')
    regs['C'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_4F(): # MOV C,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV C,A')
    regs['C'] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_50(): # MOV D,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,B')
    regs['D'] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_51(): # MOV D,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,C')
    regs['D'] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_52(): # MOV D,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,D')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_53(): # MOV D,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,E')
    regs['D'] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_54(): # MOV D,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,H')
    regs['D'] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_55(): # MOV D,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,L')
    regs['D'] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_56(): # MOV D,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,M')
    regs['D'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_57(): # MOV D,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV D,A')
    regs['D'] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_58(): # MOV E,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,B')
    regs['E'] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_59(): # MOV E,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,C')
    regs['E'] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_5A(): # MOV E,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,D')
    regs['E'] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_5B(): # MOV E,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,E')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_5C(): # MOV E,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,H')
    regs['E'] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_5D(): # MOV E,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,L')
    regs['E'] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_5E(): # MOV E,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,M')
    regs['E'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_5F(): # MOV E,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV E,A')
    regs['E'] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_60(): # MOV H,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,B')
    regs['H'] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_61(): # MOV H,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,C')
    regs['H'] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_62(): # MOV H,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,D')
    regs['H'] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_63(): # MOV H,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,E')
    regs['H'] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_64(): # MOV H,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,H')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_65(): # MOV H,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,L')
    regs['H'] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_66(): # MOV H,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,M')
    regs['H'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_67(): # MOV H,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV H,A')
    regs['H'] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_68(): # MOV L,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,B')
    regs['L'] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_69(): # MOV L,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,C')
    regs['L'] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_6A(): # MOV L,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,D')
    regs['L'] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_6B(): # MOV L,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,E')
    regs['L'] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_6C(): # MOV L,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,H')
    regs['L'] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_6D(): # MOV L,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,L')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_6E(): # MOV L,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,M')
    regs['L'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_6F(): # MOV L,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV L,A')
    regs['L'] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_70(): # MOV M,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,B')
    memory[256*regs['H'] + regs['L']] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_71(): # MOV M,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,C')
    memory[256*regs['H'] + regs['L']] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_72(): # MOV M,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,D')
    memory[256*regs['H'] + regs['L']] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_73(): # MOV M,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,E')
    memory[256*regs['H'] + regs['L']] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_74(): # MOV M,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,H')
    memory[256*regs['H'] + regs['L']] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_75(): # MOV M,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,L')
    memory[256*regs['H'] + regs['L']] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_76(): # HLT
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'HLT')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_77(): # MOV M,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV M,A')
    memory[256*regs['H'] + regs['L']] = regs['A']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_78(): # MOV A,B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,B')
    regs['A'] = regs['B']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_79(): # MOV A,C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,C')
    regs['A'] = regs['C']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_7A(): # MOV A,D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,D')
    regs['A'] = regs['D']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_7B(): # MOV A,E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,E')
    regs['A'] = regs['E']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_7C(): # MOV A,H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,H')
    regs['A'] = regs['H']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_7D(): # MOV A,L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,L')
    regs['A'] = regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_7E(): # MOV A,M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,M')
    regs['A'] = memory[256*regs['H'] + regs['L']]
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_7F(): # MOV A,A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'MOV A,A')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_80(): # ADD B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD B')
    i = regs['A'] + regs['B']
    j = (regs['A'] & 15) + (regs['B'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_81(): # ADD C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD C')
    i = regs['A'] + regs['C']
    j = (regs['A'] & 15) + (regs['C'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_82(): # ADD D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD D')
    i = regs['A'] + regs['D']
    j = (regs['A'] & 15) + (regs['D'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_83(): # ADD E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD E')
    i = regs['A'] + regs['E']
    j = (regs['A'] & 15) + (regs['E'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_84(): # ADD H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD H')
    i = regs['A'] + regs['H']
    j = (regs['A'] & 15) + (regs['H'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_85(): # ADD L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD L')
    i = regs['A'] + regs['L']
    j = (regs['A'] & 15) + (regs['L'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_86(): # ADD M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD M')
    i = regs['A'] + memory[256*regs['H'] + regs['L']]
    j = (regs['A'] & 15) + (memory[256*regs['H'] + regs['L']] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_87(): # ADD A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADD A')
    i = regs['A'] + regs['A']
    j = (regs['A'] & 15) + (regs['A'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_88(): # ADC B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC B')
    i = regs['A'] + regs['B'] + flags['CY']
    j = (regs['A'] & 15) + (regs['B'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_89(): # ADC C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC C')
    i = regs['A'] + regs['C'] + flags['CY']
    j = (regs['A'] & 15) + (regs['C'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_8A(): # ADC D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC D')
    i = regs['A'] + regs['D'] + flags['CY']
    j = (regs['A'] & 15) + (regs['D'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_8B(): # ADC E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC E')
    i = regs['A'] + regs['E'] + flags['CY']
    j = (regs['A'] & 15) + (regs['E'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_8C(): # ADC H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC H')
    i = regs['A'] + regs['H'] + flags['CY']
    j = (regs['A'] & 15) + (regs['H'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_8D(): # ADC L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC L')
    i = regs['A'] + regs['L'] + flags['CY']
    j = (regs['A'] & 15) + (regs['L'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_8E(): # ADC M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC M')
    i = regs['A'] + memory[256*regs['H'] + regs['L']] + flags['CY']
    j = (regs['A'] & 15) + (memory[256*regs['H'] + regs['L']] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_8F(): # ADC A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'ADC A')
    i = regs['A'] + regs['A'] + flags['CY']
    j = (regs['A'] & 15) + (regs['A'] & 15) + flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_90(): # SUB B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB B')
    i = regs['A'] - regs['B']
    j = (regs['A'] & 15) - (regs['B'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_91(): # SUB C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB C')
    i = regs['A'] - regs['C']
    j = (regs['A'] & 15) - (regs['C'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_92(): # SUB D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB D')
    i = regs['A'] - regs['D']
    j = (regs['A'] & 15) - (regs['D'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_93(): # SUB E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB E')
    i = regs['A'] - regs['E']
    j = (regs['A'] & 15) - (regs['E'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_94(): # SUB H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB H')
    i = regs['A'] - regs['H']
    j = (regs['A'] & 15) - (regs['H'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_95(): # SUB L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB L')
    i = regs['A'] - regs['L']
    j = (regs['A'] & 15) - (regs['L'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_96(): # SUB M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB M')
    i = regs['A'] - memory[256*regs['H'] + regs['L']]
    j = (regs['A'] & 15) - (memory[256*regs['H'] + regs['L']] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_97(): # SUB A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SUB A')
    i = regs['A'] - regs['A']
    j = (regs['A'] & 15) - (regs['A'] & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_98(): # SBB B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB B')
    i = regs['A'] - regs['B'] - flags['CY']
    j = (regs['A'] & 15) - (regs['B'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_99(): # SBB C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB C')
    i = regs['A'] - regs['C'] - flags['CY']
    j = (regs['A'] & 15) - (regs['C'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_9A(): # SBB D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB D')
    i = regs['A'] - regs['D'] - flags['CY']
    j = (regs['A'] & 15) - (regs['D'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_9B(): # SBB E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB E')
    i = regs['A'] - regs['E'] - flags['CY']
    j = (regs['A'] & 15) - (regs['E'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_9C(): # SBB H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB H')
    i = regs['A'] - regs['H'] - flags['CY']
    j = (regs['A'] & 15) - (regs['H'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_9D(): # SBB L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB L')
    i = regs['A'] - regs['L'] - flags['CY']
    j = (regs['A'] & 15) - (regs['L'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_9E(): # SBB M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB M')
    i = regs['A'] - memory[256*regs['H'] + regs['L']] - flags['CY']
    j = (regs['A'] & 15) - (memory[256*regs['H'] + regs['L']] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_9F(): # SBB A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'SBB A')
    i = regs['A'] - regs['A'] - flags['CY']
    j = (regs['A'] & 15) - (regs['A'] & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A0(): # ANA B
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA B')
    regs['A'] = regs['A'] & regs['B']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A1(): # ANA C
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA C')
    regs['A'] = regs['A'] & regs['C']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A2(): # ANA D
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA D')
    regs['A'] = regs['A'] & regs['D']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A3(): # ANA E
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA E')
    regs['A'] = regs['A'] & regs['E']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A4(): # ANA H
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA H')
    regs['A'] = regs['A'] & regs['H']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A5(): # ANA L
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA L')
    regs['A'] = regs['A'] & regs['L']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A6(): # ANA M
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA M')
    regs['A'] = regs['A'] & memory[256*regs['H'] + regs['L']]
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_A7(): # ANA A
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ANA A')
    regs['A'] = regs['A'] & regs['A']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A8(): # XRA B
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA B')
    regs['A'] = regs['A'] ^ regs['B']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_A9(): # XRA C
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA C')
    regs['A'] = regs['A'] ^ regs['C']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_AA(): # XRA D
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA D')
    regs['A'] = regs['A'] ^ regs['D']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_AB(): # XRA E
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA E')
    regs['A'] = regs['A'] ^ regs['E']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_AC(): # XRA H
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA H')
    regs['A'] = regs['A'] ^ regs['H']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_AD(): # XRA L
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA L')
    regs['A'] = regs['A'] ^ regs['L']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_AE(): # XRA M
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA M')
    regs['A'] = regs['A'] ^ memory[256*regs['H'] + regs['L']]
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_AF(): # XRA A
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XRA A')
    regs['A'] = regs['A'] ^ regs['A']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B0(): # ORA B
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA B')
    regs['A'] = regs['A'] | regs['B']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B1(): # ORA C
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA C')
    regs['A'] = regs['A'] | regs['C']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B2(): # ORA D
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA D')
    regs['A'] = regs['A'] | regs['D']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B3(): # ORA E
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA E')
    regs['A'] = regs['A'] | regs['E']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B4(): # ORA H
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA H')
    regs['A'] = regs['A'] | regs['H']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B5(): # ORA L
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA L')
    regs['A'] = regs['A'] | regs['L']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B6(): # ORA M
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA M')
    regs['A'] = regs['A'] | memory[256*regs['H'] + regs['L']]
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_B7(): # ORA A
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'ORA A')
    regs['A'] = regs['A'] | regs['A']
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B8(): # CMP B
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP B')
    i = regs['A'] - regs['B']
    j = (regs['A'] & 15) - (regs['B'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_B9(): # CMP C
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP C')
    i = regs['A'] - regs['C']
    j = (regs['A'] & 15) - (regs['C'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_BA(): # CMP D
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP D')
    i = regs['A'] - regs['D']
    j = (regs['A'] & 15) - (regs['D'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_BB(): # CMP E
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP E')
    i = regs['A'] - regs['E']
    j = (regs['A'] & 15) - (regs['E'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_BC(): # CMP H
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP H')
    i = regs['A'] - regs['H']
    j = (regs['A'] & 15) - (regs['H'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_BD(): # CMP L
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP L')
    i = regs['A'] - regs['L']
    j = (regs['A'] & 15) - (regs['L'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_BE(): # CMP M
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP M')
    i = regs['A'] - memory[256*regs['H'] + regs['L']]
    j = (regs['A'] & 15) - (memory[256*regs['H'] + regs['L']] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 7

def instruction_BF(): # CMP A
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),'CMP A')
    i = regs['A'] - regs['A']
    j = (regs['A'] & 15) - (regs['A'] & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_C0(): # RNZ
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RNZ')
    if flags['Z'] == 0:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_C1(): # POP B
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'POP B')
    sp = regs['SP']
    regs['C'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['B'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_C2(): # JNZ addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JNZ ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['Z'] == 0:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_C3(): # JMP addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JMP ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    target = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    if target == 0x23: # jump to SAVE hardware hook
        hook_save()
    regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    periods += 10

def instruction_C4(): # CNZ addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CNZ ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['Z'] == 0:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 18
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 9

def instruction_C5(): # PUSH B
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'PUSH B')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['B']
    sp = (sp - 1) & 65535
    memory[sp] = regs['C']
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 11

def instruction_C6(): # ADI D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
    'ADI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]
    i = regs['A'] + D8
    j = (regs['A'] & 15) + (D8 & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_C7(): # RST 0
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 0')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 8
    periods += 11

def instruction_C8(): # RZ
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RZ')
    if flags['Z'] == 1:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_C9(): # RET
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RET')
    sp = regs['SP']
    pc = memory[sp]
    sp = (sp + 1) & 65535
    pc += 256 * memory[sp]
    sp = (sp + 1) & 65535
    regs['PC'] = pc
    regs['SP'] = sp
    periods += 10

def instruction_CA(): # JZ addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JZ ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['Z'] == 1:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_CB(): # undefined
    print ('Undefined instuction CB encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_CC(): # CZ addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CZ ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['Z'] == 1:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 17
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 11

def instruction_CD(): # CALL addr
    global memory, periods, regs, flags, single_step, column
    global fline, fname, fsize, fload
    if single_step: print(str.format('{:04X}', regs['PC']),'CALL ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    target = memory[regs['PC']+1] + 256*memory[regs['PC']+2]

    if target == 0x20: # CALL GETLIN hardware hook
        line = input()
        KBDBFR = 65027 # 0FE03H
        ptr = 0
        for char in line:
            memory[KBDBFR+ptr] = ord(char)
            ptr += 1
        memory[KBDBFR+ptr] = 13
        column = 1
        regs['PC'] = regs['PC'] + 3
        return
    
    if target == 0x23: # CALL FLOUT hardware hook
        ptr = 256*regs['D'] + regs['E']
        msg = ''
        while memory[ptr] > 0: # Get mesage pointed to by DE
            msg += chr(memory[ptr])
            ptr +=1
        if msg == '$SIZE ': # Begin LOAD?
            fload = True
        elif fload == True:
            fname = msg
            fload = False
        elif msg[:-1] == ' LINE': # Get number of lines in file
            try:
                fp = open(fname, 'r')
                fsize = len(fp.readlines())
                fp.close()
                fsize += 1  # Flash drive overstates the size by 1
                fline = 0
            except:
                fsize = 0
            size = str(fsize)
            ptr = 0xfd00  # FDBFR
            for char in size:
                memory[ptr] = ord(char)
                ptr += 1
            memory[ptr] = 0
        elif msg == '$READ ':  # Read next line of file?
            try:
                f = open(fname)
                prog = f.readlines() # Read the text file
                f.close()
                line = prog[fline]
                fline += 1
                ptr = 0xfd03   # FDBFR memory address + 3
                for char in line:
                    if ord(char) == 0xA:  # line feed?
                        if memory[ptr-1] != 0xD:
                            memory[ptr] = 0xD    # if no CR, add it
                            ptr += 1
                    memory[ptr] = ord(char)
                    ptr +=1
                memory[0xfc3e] = ptr % 256
                memory[0xfc3f] = ptr // 256
            except:
                print('File READ error')
                # sys.exit()
        
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = ((regs['PC']+3) & 65535) // 256
    sp = (sp - 1) & 65535
    memory[sp] = ((regs['PC']+3) & 65535) % 256
    regs['SP'] = sp
    regs['PC'] = target
    periods += 17

def instruction_CE(): # ACI D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
    'ACI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1] + flags['CY']
    i = regs['A'] + D8
    j = (regs['A'] & 15) + (D8 & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    flags['CY'] = i // 256
    flags['AC'] = j // 16
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_CF(): # RST 1
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 1')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 16
    periods += 11

def instruction_D0(): # RNC
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RNC')
    if flags['CY'] == 0:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_D1(): # POP D
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'POP D')
    sp = regs['SP']
    regs['E'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['D'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_D2(): # JNC addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JNC ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['CY'] == 0:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_D3(): # OUT D8
    global memory, periods, regs, flags, column, save_flag, save_program, fname
    if single_step: print(str.format('{:04X}', regs['PC']),
    'OUT ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]
    port[D8] = regs['A']
    if D8 == 2: # Hardware hook: port 2 mapped to UART data
        if regs['A'] == 10: #ignore LF
            pass
        elif regs['A'] == 13: #handle CR
            print('\n', end='')
            column = 1
        else:
            print(chr(regs['A']), end = '', flush=True)
            column += 1
            if column > columns:
                print('\n', end='')
                column = 1
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 10

def instruction_D4(): # CNC addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CNC ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['CY'] == 0:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 17
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 11

def instruction_D5(): # PUSH D
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'PUSH D')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['D']
    sp = (sp - 1) & 65535
    memory[sp] = regs['E']
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 11

def instruction_D6(): # SUI D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
    'SUI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]    
    i = regs['A'] - D8
    j = (regs['A'] & 15) - (D8 & 15)
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_D7(): # RST 2
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 2')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 24
    periods += 11

def instruction_D8(): # RC
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RC')
    if flags['CY'] == 1:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_D9(): # undefined
    print ('Undefined instuction D9 encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_DA(): # JC addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JC ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['CY'] == 1:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_DB(): # IN D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
    'IN ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]
    if D8 == 3: # Hardware hook: port 3 mapped to UART status (1)
        regs['A'] = 1
    else:
        regs['A'] = port[D8]
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 10

def instruction_DC(): # CC addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CC ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['CY'] == 1:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 17
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 11

def instruction_DD(): # undefined
    print ('Undefined instuction DD encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_DE(): # SBI D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
    'SBI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]    
    i = regs['A'] - D8 - flags['CY']
    j = (regs['A'] & 15) - (D8 & 15) - flags['CY']
    regs['A'] = i & 255
    set_flags_ZSP(regs['A'])
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_DF(): # RST 3
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 3')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 32
    periods += 11

def instruction_E0(): # RPO
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RPO')
    if flags['P'] == 0:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_E1(): # POP H
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'POP H')
    sp = regs['SP']
    regs['L'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['H'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_E2(): # JPO addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JPO ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['P'] == 0:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_E3(): # XTHL
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XTHL')
    th = regs['H']
    tl = regs['L']
    regs['H'] = memory[regs['SP']+1]
    regs['L'] = memory[regs['SP']]
    memory[regs['SP']+1] = th
    memory[regs['SP']] = tl
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 18

def instruction_E4(): # CPO addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CPO ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['P'] == 0:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 17
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 11

def instruction_E5(): # PUSH H
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'PUSH H')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['H']
    sp = (sp - 1) & 65535
    memory[sp] = regs['L']
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 11

def instruction_E6(): # ANI D8
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),
    'ANI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]    
    regs['A'] = regs['A'] & D8
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 4

def instruction_E7(): # RST 4
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 4')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 40
    periods += 11

def instruction_E8(): # RPE
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RPE')
    if flags['P'] == 1:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_E9(): # PCHL
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'PCHL')
    regs['PC'] = 256* regs['H'] + regs['L']
    periods += 5

def instruction_EA(): # JPE addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JPO ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['P'] == 1:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_EB(): # XCHG
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'XCHG')
    th = regs['H']
    tl = regs['L']
    regs['H'] = regs['D']
    regs['L'] = regs['E']
    regs['D'] = th
    regs['E'] = tl
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_EC(): # CPE addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CPE ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['P'] == 1:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 17
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 11

def instruction_ED(): # undefined
    print ('Undefined instuction ED encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_EE(): # XRI D8
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),
    'XRI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]    
    regs['A'] = regs['A'] ^ D8
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 4

def instruction_EF(): # RST 5
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 5')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 48
    periods += 11

def instruction_F0(): # RP
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RP')
    if flags['S'] == 0:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 12
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 6

def instruction_F1(): # POP PSW
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'POP PSW')
    sp = regs['SP']
    i = memory[sp]
    flags['S'] = (i & 128) // 128
    flags['Z'] = (i & 64) // 64
    flags['K'] = (i & 32) // 32
    flags['AC'] = (i & 16) // 16
    flags['P'] = (i & 4) // 4
    flags['V'] = (i & 2) // 2
    flags['CY'] = (i & 1)
    sp = (sp + 1) & 65535
    regs['A'] = memory[sp]
    sp = (sp + 1) & 65535
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 10

def instruction_F2(): # JP addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JP ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['S'] == 0:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 7

def instruction_F3(): # DI
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'DI')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_F4(): # CP addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CP ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['S'] == 0:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 18
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 9

def instruction_F5(): # PUSH PSW
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'PUSH PSW')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['A']
    sp = (sp - 1) & 65535
    i = 0
    i += flags['S'] * 128
    i += flags['Z'] * 64
    i += flags['K'] * 32
    i += flags['AC'] * 16
    i += flags['P'] * 4
    i += flags['V'] * 2
    i += flags['CY']
    memory[sp] = i
    regs['SP'] = sp
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 11

def instruction_F6(): # ORI D8
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),
    'ORI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]    
    regs['A'] = regs['A'] | D8
    set_flags_ZSP(regs['A'])
    flags ['CY'] = 0
    flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 4

def instruction_F7(): # RST 6
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 6')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 56
    periods += 11

def instruction_F8(): # RM
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RM')
    if flags['S'] == 1:
        sp = regs['SP']
        pc = memory[sp]
        sp += 1
        pc += 256 * memory[sp]
        sp += 1
        regs['PC'] = pc
        regs['SP'] = sp
        periods += 11
    else:
        regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_F9(): # SPHL
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'SPHL')
    regs['SP'] = 256*regs['H'] + regs['L']
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 5

def instruction_FA(): # JM addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'JM ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['S'] == 1:
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 10
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 10

def instruction_FB(): # EI
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'EI')
    regs['PC'] = (regs['PC'] + 1) & 65535
    periods += 4

def instruction_FC(): # CM addr
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'CM ' + \
        str.format('{:02X}', memory[regs['PC']+2]) + \
        str.format('{:02X}', memory[regs['PC']+1]))
    if flags['S'] == 1:
        sp = regs['SP']
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) // 256
        sp = (sp - 1) & 65535
        memory[sp] = ((regs['PC']+3) & 65535) % 256
        regs['SP'] = sp
        regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
        periods += 17
    else:
        regs['PC'] = (regs['PC'] + 3) & 65535
    periods += 11

def instruction_FD(): # undefined
    print ('Undefined instuction 0FD encountered at', str.format('{:04X}', regs['PC']))
    global invalid
    invalid = True

def instruction_FE(): # CPI D8
    global memory, periods, regs, flags
    if single_step: print(str.format('{:04X}', regs['PC']),
    'CPI ' + str.format('{:02X}', memory[regs['PC']+1]))
    D8 = memory[regs['PC']+1]    
    i = regs['A'] - D8
    j = (regs['A'] & 15) - (D8 & 15)
    set_flags_ZSP(i & 255)
    if i < 0: flags['CY'] = 1
    else: flags['CY'] = 0
    if j < 0: flags['AC'] = 1
    else: flags['AC'] = 0
    regs['PC'] = (regs['PC'] + 2) & 65535
    periods += 7

def instruction_FF(): # RST 7
    global memory, periods, regs, flags, single_step
    if single_step: print(str.format('{:04X}', regs['PC']),'RST 7')
    sp = regs['SP']
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] % 256
    sp = (sp - 1) & 65535
    memory[sp] = regs['PC'] // 256
    regs['SP'] = sp
    regs['PC'] = 64
    periods += 11


#-----------------------------------------------------------------------------#
def execute_program(list):
    """Execute program loaded into memory array"""
    global periods, regs, memory, flags, invalid
    periods = 0
    if len(list) == 2:
        regs['PC'] = address(list[1])
    while regs['PC'] != breakpoint and invalid == False:
        #Read and execute next instruction pointed to by PC
        if regs['PC'] > 2**16 - 1:
            print('Error - invalid PC')
            break
        op_code = hex(memory[regs['PC']]).lstrip("0x").zfill(2).upper()
        eval("instruction_" + op_code + "()")
        if op_code == '76': # HLT?
            break
    if regs['PC'] == breakpoint:
        print('Break point reached')
    if invalid == True:
        invalid = False
    print('Halted.  Total time periods =', periods)

def execute_single(list):
    """"Execute single step of program"""
    global single_step, regs, memory, flags
    if len(list) == 2:
        regs['PC'] = address(list[1])
    if regs['PC'] > 2**16 - 1:
        print('Error - invalid PC')
        return
    single_step = 1
    op_code = hex(memory[regs['PC']]).lstrip("0x").zfill(2).upper()
    eval("instruction_" + op_code + "()")
    single_step = 0

def open_file(file_name, mode):
    """"Open a file."""
    try:
        the_file = open(file_name, mode)
    except IOError as e:
        print("\n*** Unable to open the file", file_name, "\n", e)
        sys.exit()
    else:
        return the_file

def command_list(command):
    """"Make a list with upper case command + optional parameters"""
    list = command.split()
    if len(list) == 0:
        list.append('')
    for i in range (len(list)):
        if i==1 and list[0]== 'L': # Don't change case of LOAD parameter (file name)
            pass
        else:
            list[i] = list[i].upper()
    return list

def address(str):
    """Convert string to int 16 bit address"""
    result = error
    try:
        add = int(str, 16)
        if add < 0 or add > 2**16-1:
            print('Invalid number:', str)
        else:
            result = add
    except:
        print('Invalid number:', str)
    return result

def breakpoint_set(list):
    """Set the breakpoint address"""
    global breakpoint
    if len(list) == 1:
        print(str.format('{:04X}', breakpoint))
    if len(list) == 2:
        breakpoint = address(list[1])

def flag_set(list):
    """Set one of the flags"""
    global flags
    try:
        flag = list[1]
        bit = int(list[2])
        if bit == 0 or bit == 1:
            flags[flag] = bit
    except:
        print('Unrecognized command')

def display_memory(list):
    """Display the memory contents"""
    global memory
    if len(list) == 1:
        return
    if len(list) == 2:
        m1 = address(list[1])
        if m1 != error:
            print(str.format('{:04X}', m1), str.format('{:02X}', memory[m1]))
    else:
        m1 = address(list[1])
        m2 = address(list[2])
        if m1 != error and m2 != error:
            if m1 > m2:
                print('Invalid memory range')
                return
            for i in range(m1, m2+1):
                if i == m1 or i%16 == 0:
                    print(str.format('{:04X}', i)+': ', end='')
                print(str.format('{:02X}', memory[i])+' ', end='')
                if i%16 == 15:
                    print()
            if i%16 != 15:
                print()

def memory_set(list):
    """Set memory value(s)"""
    try:
        addr = int(list[1], 16)
        if addr < 0 or addr > 65535:
            print('Invalid address')
            return
        for i in range(len(list)-2):
            byte = int(list[i+2], 16)
            if byte < 0 or byte > 255:
                print('Invalid byte value')
                return
            memory[addr] = byte
            addr += 1
    except:
        print('Value error')

def port_set(list):
    """Display/Set port value"""
    global ports
#    if len(list) == 2:

        
def display_help():
    """Display help message"""
    print('Commands')
    print('  B Addr        ;Set breakpoint address')
    print('  C             ;Clear breakpoint')
    print('  D Addr (Addr) ;Display memory range')
    print('  E Addr        ;Execute from address')
    print('  F flag bit    ;Set flags (CY, V, P, AC, K, S, Z)')
    print('  H(elp)        ;Display help')
    print('  L name.hex    ;Load hex file')
    print('  M Addr byte (byte) ;Set memory')
    print('  P Port (byte) ;Display/Set I/O port')
    print('  Q(uit)        ;Quit')
    print('  R             ;Display registers (A, BC, DE, HL, PSW, PC, SP)')
    print('  S (Addr)      ;Single step from address or current PC')

def rform(reg):
    """Format register content"""
    global regs
    return str.format('{:02X}', regs[reg])
    
def display_registers(list):
    """Display or modify registers and display flags"""
    global regs, flags
    if len(list) == 1:
        print("A  ", rform("A"),           "\t\tCY", flags["CY"])
        print("BC ", rform("B"), rform("C"), "\tV ", flags["V"])
        print("DE ", rform("D"), rform("E"), "\tP ", flags["P"])
        print("HL ", rform("H"), rform("L"), "\tAC", flags["AC"])
        print("PC ", str.format('{:04X}', regs["PC"]), "\tK ", flags["K"])
        print("SP ", str.format('{:04X}', regs["SP"]), "\tZ ", flags["Z"])
        flag_byte = flags["CY"] + 2*flags["V"] + 4*flags["P"] + 16*flags["AC"] \
            + 32*flags["K"] + 64*flags["Z"] + 128*flags["S"]
        PSW = rform("A") + str.format('{:02X}', flag_byte)
        print("PSW", PSW, "\tS ", flags["S"])
    else:
        try:
            reg = list[1]
            byte = list[2]
            val = int(byte, 16)
            if reg == 'SP' or reg == 'PC':
                if val < 0 or val > 65535:
                    print('Value out of range')
                    return
            else:
                if val < 0 or val > 255:
                    print('Value out of range')
                    return
            regs[reg] = val
        except:
            print('Unrecognized command')

def load_file(list):
    """Load the hex file into memory
    Ref: https://en.wikipedia.org/wiki/Intel_HEX
    """
    file_name = list[1]
    try:
        hexfile = open_file(file_name, 'r')
        while True:
            str = hexfile.readline()
            bytes = int(str[1:3], 16)
            if bytes == 0:
                break
            address = int(str[3:7], 16)
            for i in range(bytes):
                byte = int(str[9+2*i:11+2*i], 16)
                memory[address] = byte
                address += 1
        hexfile.close()
    except:
        print('Invalid or missing file')

def hook_save():
    """Hardware hook simulates save to flash drive"""
    text = ""
    keyboard_buffer = 0xFE00
    basbeg = 0x8000
    basend_ptr = 0xFC00
    basend = memory[basend_ptr]+256*memory[basend_ptr+1]
    for i in range (255):
        text += chr(memory[keyboard_buffer+i])
    try:
        pos1 = text.find('\x9C')+1     #pos of SAVE token+1
        pos2 = text.find('\x00', pos1) #pos of end of line
        file_name = text[pos1:pos2].lstrip()
        f = open(file_name, "w")
        f.write("this is a test\n")
        f.close()
    except:
        print('Emulator Save Error')
    regs['PC'] = memory[regs['PC']+1] + 256*memory[regs['PC']+2]
    memory[regs['PC']+1] = 0xAF # Ptr to PROMPT: is 00AFH
    memory[regs['PC']+2] = 0x00
    return



#----------------------------------------------------------------------
# main
#----------------------------------------------------------------------

print("\n--- Emulator for Intel 8080A microprocessor ---")
if len(sys.argv) > 1: # handle optional argv for load file
    fname = sys.argv[1].strip()
    cl = ['L', fname]
    load_file(cl)
while True:
    command_string = input(".")
    cl = command_list(command_string)
    command = cl[0]
    if command == "B":
        breakpoint_set(cl)
    if command == "C":
        breakpoint = -1
    if command == "D":
        display_memory(cl)
    if command == "E":
        execute_program(cl)
    if command == "F":
        flag_set(cl)
    if command == "H" or cl[0] == "HELP":
        display_help()
    if command == "L":
        load_file(cl)
    if command == "M":
        memory_set(cl)
    if command == "P":
        port_set(cl)
    if command == "R":
        display_registers(cl)
    if command == "S":
        execute_single(cl)
    if command == "Q" or cl[0] == "QUIT":
        break
