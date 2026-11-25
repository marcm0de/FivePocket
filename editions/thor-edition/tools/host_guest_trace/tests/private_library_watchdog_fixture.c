#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <time.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

// Original asset-free deliberately non-returning library. The app must kill
// only its dedicated debug process; never label this fixture as game execution.
__attribute__((visibility("default")))
int recomp_run_private_trace_v1(int argc,char** argv){
    (void)argc;(void)argv;
#if defined(WATCHDOG_FILE)
    // Give the external harness time to observe the admitted original PID.
    struct timespec ready={1,0};nanosleep(&ready,0);
    const int fd=open("/data/data/dev.recompbootstrap.smoke/files/private-trace/file-limit-probe",
                      O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd<0)_exit(43);
    const char byte=1;const ssize_t result=write(fd,&byte,1);const int error=errno;
    struct stat info;
    const int valid=result==-1 && error==EFBIG && !fstat(fd,&info) && info.st_size==0;
    close(fd);_exit(valid?42:43);
#elif defined(WATCHDOG_CPU)
    volatile uint64_t counter=0;
    for(;;)++counter;
#else
    struct timespec pause={1,0};
    for(;;)nanosleep(&pause,0);
#endif
    return 0;
}
