"""Strict Python reader/writer for v2 scope values; occurrence metadata is not data."""
import csv
from pathlib import Path
csv.field_size_limit(64*1024*1024)

def header(path):
    with Path(path).open(encoding='utf-8',newline='') as stream:
        fields=next(csv.reader(stream))
    if fields[:2]!=['#multimodel-scope-v2','defined'] or len(set(fields[2:]))!=len(fields[2:]):
        raise ValueError('Invalid masked header')
    return fields[2:]

def rows(path):
    with Path(path).open(encoding='utf-8',newline='') as stream:
        reader=csv.reader(stream); fields=next(reader); columns=fields[2:]
        if fields[:2]!=['#multimodel-scope-v2','defined']: raise ValueError('Invalid masked header')
        for i,record in enumerate(reader):
            if len(record)!=len(columns)+2 or record[0]!=str(i) or len(record[1])!=len(columns):
                raise ValueError('Invalid masked record')
            values=[]
            for bit,value in zip(record[1],record[2:]):
                if bit not in '01' or (bit=='0' and value!=''): raise ValueError('Invalid missing payload')
                values.append(value if bit=='1' else None)
            yield values

def write(path,columns,records):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with Path(path).open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream,quoting=csv.QUOTE_ALL,lineterminator='\n')
        writer.writerow(['#multimodel-scope-v2','defined',*columns])
        for i,row in enumerate(records):
            if len(row)!=len(columns): raise ValueError('Wrong row width')
            writer.writerow([i,''.join('0' if v is None else '1' for v in row),*('' if v is None else v for v in row)])
