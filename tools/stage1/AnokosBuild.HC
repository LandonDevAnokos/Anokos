//Anokos stage-1 build. Runs inside stock TempleOS (booted from the
//stage-1 ISO made by tools/anokos_build.py), compiles the Anokos
//kernel from ::/AnokosSrc and sends it to the host over COM1.

#define SER_BASE	0x3F8

U0 SerInit()
{//115200 8N1, FIFOs on.
  OutU8(SER_BASE+1,0);
  OutU8(SER_BASE+3,0x80);
  OutU8(SER_BASE+0,1);
  OutU8(SER_BASE+1,0);
  OutU8(SER_BASE+3,3);
  OutU8(SER_BASE+2,0xC7);
  OutU8(SER_BASE+4,0x0B);
}

U0 SerPutC(U8 ch)
{
  I64 i=0;
  while (!(InU8(SER_BASE+5)&0x20) && i++<1000000);
  OutU8(SER_BASE,ch);
}

U0 SerPutS(U8 *st)
{
  while (*st)
    SerPutC(*st++);
}

U0 SerPrint(U8 *fmt,...)
{
  U8 *buf=StrPrintJoin(NULL,fmt,argc,argv);
  SerPutS(buf);
  Free(buf);
}

Bool SerMirrorPutKey(I64 ch,I64)
{//Copy all screen output to the serial log.
  if (ch) SerPutC(ch);
  return FALSE;
}

Bool SerMirrorPutS(U8 *st)
{
  SerPutS(st);
  return FALSE;
}

Bool SerSendFile(U8 *filename,U8 *tag)
{
  I64 size,i;
  U8 *buf,*hex="0123456789ABCDEF";
  if (!FileFind(filename) || !(buf=FileRead(filename,&size))) {
    SerPrint("\n@@NOFILE %s %s\n",tag,filename);
    return FALSE;
  }
  SerPrint("\n@@BEGIN %s %d\n",tag,size);
  for (i=0;i<size;i++) {
    SerPutC(hex[buf[i]>>4]);
    SerPutC(hex[buf[i]&15]);
    if ((i&63)==63)
      SerPutC('\n');
  }
  SerPrint("\n@@END %s\n",tag);
  Free(buf);
  return TRUE;
}

I64 anokos_errs=-1;

SerInit;
KeyDevAdd(&SerMirrorPutKey,&SerMirrorPutS,0x10000000,FALSE);
SerPrint("\n@@STAGE1 START\n");
try {
  DelTree("B:/Kernel");
  DelTree("B:/Compiler");
  CopyTree("::/AnokosSrc/Kernel","B:/Kernel");
  CopyTree("::/AnokosSrc/Compiler","B:/Compiler");
  Cd("B:/");
  //DoDistro's config answers, plus one Enter for the hardware probe prompt.
  In("TB\nScale2Mem(2048,0x40000)\nT\n\n\n\n\n");
  anokos_errs=Cmp("/Kernel/Kernel","Kernel",,':');
} catch
  PutExcept;
SerPrint("\n@@ERRORS %d\n",anokos_errs);
if (!anokos_errs) {
  SerSendFile("B:/Kernel/Kernel.BIN.Z","KERNEL");
  if (!SerSendFile("B:/Kernel/Kernel.MAP.Z","MAP"))
    SerSendFile("B:/Kernel.MAP.Z","MAP");
}
SerPrint("\n@@STAGE1 DONE\n");
