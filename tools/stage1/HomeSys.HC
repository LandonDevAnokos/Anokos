//Stage-1 build ISO only: stock TempleOS start-up, except the first
//terminal runs the Anokos kernel build instead of the install prompt.

U0 UserStartUp()
{
  DocTermNew;
  LBts(&Fs->display_flags,DISPLAYf_SHOW);
  WinToTop;
  WinZBufUpdate;
}

U0 SrvStartUp()
{
  DocTermNew;
  LBts(&Fs->display_flags,DISPLAYf_SHOW);
  WinToTop;
  WinZBufUpdate;
}

U0 StartUpTasks()
{
  CTask *user1;
  user1=User;
  WinToTop(user1);
  WinMax(user1);
  XTalk(user1,"Cd(\"::/\");#include \"::/AnokosBuild\";\n");
}

StartUpTasks;
