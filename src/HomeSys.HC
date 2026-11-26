//Place this file in /Home and change
//anything you want.

//Anokos: with a VBE framebuffer the system boots to the full-screen
//Anokos terminal and has no windows, so the window setup below only
//runs on the fallback TempleOS desktop.

U0 UserStartUp()
{//Run each time a user a spawned
  if (Bt(&sys_run_level,RLf_VBE))
    return;
  DocTermNew;
  Type("::/Doc/Start.DD");
  LBts(&Fs->display_flags,DISPLAYf_SHOW);
  WinToTop;
  WinZBufUpdate;
  Dir;
}

U0 SrvStartUp()
{//Run each time a srv task is spawned.
  if (Bt(&sys_run_level,RLf_VBE))
    return;
  DocTermNew;
  LBts(&Fs->display_flags,DISPLAYf_SHOW);
  WinToTop;
  WinZBufUpdate;
}

U0 StartUpTasks()
{
  CTask *user1,*user2;
  user1=User;
  user2=User;
  WinToTop(user1);
  WinTileVert;
  "Boot Time:%7.3fs\n",tS;
  XTalk(user1,"Cd;#include \"Once\";\n");
  Silent;	//no output to scrn
  ACInit("/*;!*/Bible.TXT*");
  Silent(OFF); //no output to scrn
}

if (!Bt(&sys_run_level,RLf_VBE)) {
  StartUpTasks;
  "\nTempleOS V%5.3f\t%D %T\n\n",sys_os_version,sys_compile_time,sys_compile_time;
}
