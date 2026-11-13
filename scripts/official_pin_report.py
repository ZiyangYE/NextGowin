"""Read the public CLI HTML pin table with the same strict fixed-I/O checks."""
from html.parser import HTMLParser
from audit_ownpr_native_export import io_check

HEADER=['Port Name','Diff Pair','Loc./Bank','Constraint','Dir.','Site','CFG','IO Type','Drive','Pull Mode','PCI Clamp','Hysteresis','Open Drain','Vref','Single Resistor','Diff Resistor','BankVccio','Pull Strength','Ctle']

class Rows(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None

def audit(text,contract):
    parser=Rows();parser.feed(text)
    headers=[i for i,r in enumerate(parser.rows) if r==HEADER]
    if len(headers)!=1:raise ValueError('Public pin table header differs')
    rows=[r for r in parser.rows[headers[0]+1:] if r and r[0].lstrip('\\') in contract['pins']]
    if any(len(r)!=len(HEADER) or any('|' in v or '\n' in v for v in r) for r in rows):raise ValueError('Malformed public pin report row')
    return dict(io_check('\n'.join(' | '.join(r) for r in rows),contract),source_format='official_cli_html')
