#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <time.h>
#include <unistd.h>

// Asset-free exercise of the app's bounded pipe/return envelope, not GTA output.
__attribute__((visibility("default")))
int recomp_run_private_trace_v1(int argc,char** argv){
    (void)argc;(void)argv;
    struct timespec ready={1,0};nanosleep(&ready,0);
    fputs("PRIVATE_STDERR_MUST_NOT_ENTER_PROTOCOL\n",stderr);
#if defined(PROTOCOL_OVERFLOW)
    char buffer[8192]={0};
    for(int i=0;i<9;++i)if(write(STDOUT_FILENO,buffer,sizeof(buffer))<0)return 7;
#else
    fputs("PRIVATE_APP_PROTOCOL_FIXTURE_V1\n",stdout);
#endif
    return 0;
}
