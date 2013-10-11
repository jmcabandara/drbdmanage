#!/usr/bin/python

__author__="raltnoeder"
__date__ ="$Oct 10, 2013 9:57:03 AM$"

class ConfFile(object):
    _input = None
    
    def __init__(self, stream):
        self._input = stream
    
    def get_conf(self):
        input        = self._input
        split_idx    = self._split_idx
        unescape     = self._unescape
        extend_line  = self._extend_line
        comment_line = self._comment_line
        
        key  = None
        val  = None
        conf = dict()
        
        while True:
            line = input.readline()
            if not len(line) > 0:
                break
            
            if line.endswith("\n"):
                line = line[:len(line) - 1]
            if key is None:
                # new key/val line
                # check for comment lines
                if comment_line(line):
                    continue
                idx = split_idx(line, '=')
                if idx != -1:
                    raw_key = line[:idx]
                    raw_val = line[idx + 1:]
                    key = unescape(raw_key)
                    val = unescape(raw_val)
                else:
                    # TODO: bad line, no key/val pair
                    continue
            else:
                # val continuation line
                val += unescape(line)
            if not extend_line(raw_val):
                conf[key] = val
                key = None
                val = None
        if key is not None:
            conf[key] = val
        return conf
    
    
    def _split_idx(self, line, s_char):
        lidx = 0
        idx  = 0
        split_idx = -1
        midx = len(line) - 1
        while idx != -1:
            bidx = line.find('\\', lidx)
            sidx = line.find(s_char, lidx)
            idx = self._min_idx(bidx, sidx)
            if idx != -1:
                fchar = line[idx]
                if fchar == '\\':
                    lidx = idx + 2
                elif fchar == s_char:
                    split_idx = sidx
                    break
                if lidx > midx:
                    break
        return split_idx
    
    
    def _comment_line(self, line):
        rc = False
        idx  = 0
        midx = len(line)
        while idx < midx:
            c = line[idx]
            if not (c == ' ' or c == '\t'):
                if c == '#':
                    rc = True
                break
            idx += 1
        return rc
    
    
    def _min_idx(self, x, y):
        if x < y:
            idx = x if x != -1 else y
        else:
            idx = y if y != -1 else x
        return idx
    
    
    def _unescape(self, line):
        u_line = ""
        lidx = 0
        idx  = 0
        midx = len(line)
        # remove leading tabs and spaces
        while idx < midx:
            c = line[idx]
            if not (c == ' ' or c == '\t'):
                line = line[idx:]
                break
            idx += 1
        # replace escape sequences
        midx = len(line) - 1
        while idx != -1:
            idx = line.find('\\', lidx)
            if idx != -1:
                u_line += line[lidx:idx]
                if idx < midx:
                    cchar = line[idx + 1]
                    if cchar == 'n':
                        u_line += '\n'
                    elif cchar == 't':
                        u_line += '\t'
                    else:
                        u_line += cchar
                    lidx = idx + 2
                else:
                    # line ends with backslash, remove the backslash
                    lidx = len(line)
        # remove trailing spaces
        idx = lidx
        spaces = True
        while idx <= midx:
            c = line[idx]
            if spaces:
                if not (c == ' ' or c == '\t'):
                    spaces = False
                    u_line += line[lidx:idx]
                    lidx = idx
            else:
                if (c == ' ' or c == '\t'):
                    spaces = True
                    u_line += line[lidx:idx]
                    lidx = idx
            idx += 1
        if not spaces:
            u_line += line[lidx:]
        return u_line
    
    
    def _extend_line(self, line):
        rc   = False
        lidx = 0
        idx  = 0
        midx = len(line) - 1
        while idx != -1:
            idx = line.find("\\", lidx)
            if idx != -1:
                if idx >= midx:
                    rc = True
                    break
                else:
                    lidx = idx + 2
            if lidx > midx:
                break
        return rc