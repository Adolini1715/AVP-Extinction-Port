#!/usr/bin/env python3
"""Demangle CodeWarrior (PS2 EE) C++ names, the scheme the PS2 build's symbol
table uses:  Method__<len>Class[C]F<params>   or   Func__F<params>
Specials: __ct ctor, __dt dtor, __as operator=, ... (see OPS).

    demangle('CommandMove__11GAvPPointerFP17CZArray<7CVector>')
    -> ('GAvPPointer', 'CommandMove', ['CZArray<CVector> *'], False)

Returns (class or None, method, [param types], is_const) or None when the
name is not mangled. Unsupported shapes raise ValueError (counted by main).
"""
import re, sys

OPS = {'__ct': None, '__dt': None, '__as': 'operator=', '__eq': 'operator==',
       '__ne': 'operator!=', '__nw': 'operator new', '__dl': 'operator delete',
       '__nwa': 'operator new[]', '__dla': 'operator delete[]', '__vc': 'operator[]',
       '__cl': 'operator()', '__pl': 'operator+', '__mi': 'operator-', '__ml': 'operator*',
       '__dv': 'operator/', '__lt': 'operator<', '__gt': 'operator>', '__le': 'operator<=',
       '__ge': 'operator>=', '__apl': 'operator+=', '__ami': 'operator-=', '__amu': 'operator*=',
       '__adv': 'operator/=', '__nt': 'operator!', '__ad': 'operator&', '__rf': 'operator->',
       '__md': 'operator%', '__or': 'operator|', '__er': 'operator^', '__aa': 'operator&&',
       '__oo': 'operator||', '__ls': 'operator<<', '__rs': 'operator>>', '__pp': 'operator++',
       '__mm': 'operator--', '__co': 'operator~', '__aor': 'operator|=', '__aad': 'operator&=',
       '__aer': 'operator^=', '__als': 'operator<<=', '__ars': 'operator>>=', '__amd': 'operator%='}
BUILTIN = {'v': 'void', 'b': 'bool', 'c': 'char', 's': 'short', 'i': 'int', 'l': 'long',
           'x': 'long long', 'f': 'float', 'd': 'double', 'r': 'long double', 'w': 'wchar_t',
           'e': '...'}


class P:
    def __init__(self, s):
        self.s, self.i = s, 0

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ''

    def take(self, n=1):
        t = self.s[self.i:self.i + n]
        self.i += n
        return t

    def num(self):
        m = re.match(r'\d+', self.s[self.i:])
        if not m:
            raise ValueError('number expected at %d in %s' % (self.i, self.s))
        self.i += len(m.group(0))
        return int(m.group(0))

    def name(self):
        """<len><chars>, template arguments demangled inside <...>."""
        n = self.num()
        raw = self.take(n)
        return clean_template(raw)

    def qualified(self):
        if self.peek() == 'Q':
            self.take()
            cnt = self.num() if self.peek().isdigit() and self.s[self.i + 1:self.i + 2] and not self.s[self.i + 1].isdigit() else int(self.take())
            parts = [self.name() for _ in range(cnt)]
            return '::'.join(parts)
        return self.name()

    def type(self):
        quals = []
        while True:
            c = self.peek()
            if c == 'C':
                self.take(); quals.append('const')
            elif c == 'V':
                self.take(); quals.append('volatile')
            else:
                break
        c = self.peek()
        if c == 'P':
            self.take(); t = self.type() + ' *'
        elif c == 'R':
            self.take(); t = self.type() + ' &'
        elif c == 'U':
            self.take(); t = 'unsigned ' + BUILTIN[self.take()]
        elif c == 'S':
            self.take(); t = 'signed ' + BUILTIN[self.take()]
        elif c == 'A':
            self.take(); n = self.num(); self.take()  # '_'
            t = self.type() + '[%d]' % n
        elif c == 'F':
            self.take(); args = self.params(stop='_'); self.take(); ret = self.type()
            t = '%s (*)(%s)' % (ret, ', '.join(args))
        elif c == 'M':
            self.take(); cls = self.qualified(); inner = self.type()
            t = '%s %s::*' % (inner, cls)
        elif c == 'Q' or c.isdigit():
            t = self.qualified()
        elif c in BUILTIN:
            t = BUILTIN[self.take()]
        else:
            raise ValueError('type code %r in %s' % (c, self.s))
        if quals:
            t = ' '.join(quals) + ' ' + t if not t.endswith('*') and not t.endswith('&') else t.replace(' *', ' * ' + ' '.join(quals)).replace(' &', ' &')
        return t

    def params(self, stop=None):
        out = []
        while self.i < len(self.s) and self.peek() != stop:
            out.append(self.type())
        return out


def clean_template(raw):
    """CZArray<7CVector> -> CZArray<CVector>; template args are themselves mangled."""
    if '<' not in raw:
        return raw
    head, rest = raw.split('<', 1)
    inner = rest[:-1] if rest.endswith('>') else rest
    args, depth, cur = [], 0, ''
    for ch in inner:
        if ch == ',' and depth == 0:
            args.append(cur); cur = ''
            continue
        depth += ch == '<'
        depth -= ch == '>'
        cur += ch
    args.append(cur)
    out = []
    for a in args:
        try:
            p = P(a)
            out.append(p.type() if a and not a.lstrip('-').isdigit() else a)
        except Exception:
            out.append(a)
    return '%s<%s>' % (head, ', '.join(out))


def demangle(m):
    mm = re.match(r'^(__[a-z]+|\w+?)__((?:Q\d|\d).*)$', m)
    if mm:
        meth, rest = mm.group(1), mm.group(2)
        p = P(rest)
        cls = p.qualified()
        const = False
        if p.peek() == 'C':
            p.take(); const = True
        if p.peek() != 'F':
            if p.i == len(p.s):          # static data member, not a function
                return None
            raise ValueError('F expected in ' + m)
        p.take()
        args = p.params()
        if meth in OPS:
            short = cls.split('::')[-1].split('<')[0]
            meth = OPS[meth] or (short if meth == '__ct' else '~' + short)
        return cls, meth, [a for a in args if a != 'void'], const
    mm = re.match(r'^(\w+?)__F(.*)$', m)
    if mm:
        p = P(mm.group(2))
        return None, mm.group(1), [a for a in p.params() if a != 'void'], False
    return None


if __name__ == '__main__':
    ok = bad = 0
    fails = []
    for line in open(sys.argv[1]):
        p = line.split()
        if len(p) < 8 or p[3] != 'FUNC':
            continue
        try:
            r = demangle(p[7])
            ok += r is not None
        except Exception as e:
            bad += 1
            fails.append(p[7])
    print('demangled', ok, 'failed', bad)
    for f in fails[:15]:
        print('  ', f)
