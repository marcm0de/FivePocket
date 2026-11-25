#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    if (strcmp(argv[1], "signal") == 0) raise(SIGABRT);
    if (strcmp(argv[1], "hang") == 0) for (;;) pause();
    if (strcmp(argv[1], "cpu") == 0) for (;;) {}
    if (strcmp(argv[1], "image") == 0 || strcmp(argv[1], "image-overflow") == 0) {
        struct rlimit limit;
        if(getrlimit(RLIMIT_FSIZE,&limit)!=0 || limit.rlim_cur!=limit.rlim_max)return 3;
        int file=open("synthetic-image.bin",O_WRONLY|O_CREAT|O_EXCL,0600);
        if(file<0)return 4;
        char bytes[4096]={0};
        for(unsigned i=0;i<900;++i)if(write(file,bytes,sizeof(bytes))!=(ssize_t)sizeof(bytes))return 5;
        if(strcmp(argv[1],"image-overflow")==0 && write(file,bytes,1)!=1)return 6;
        if(close(file)!=0)return 7;
    }
    if (strcmp(argv[1], "flood") == 0) {
        char bytes[8192] = {0};
        for (;;) if (write(1, bytes, sizeof(bytes)) < 0) return 2;
    }
    if (strcmp(argv[1], "large") == 0) {
        char bytes[1000];memset(bytes,'x',sizeof(bytes));
        for(unsigned i=0;i<70;++i)if(write(1,bytes,sizeof(bytes))!=(ssize_t)sizeof(bytes))return 2;
    }
    if (strcmp(argv[1], "descendant") == 0) {
        pid_t child = fork();
        if (child < 0) return 2;
        if (child == 0) {
            close(0); close(1); close(2);
            signal(SIGTERM, SIG_IGN);
            for (;;) pause();
        }
        printf("%ld\n", (long)child);
        return 0;
    }
    if (strcmp(argv[1], "limits") == 0) {
        struct rlimit limits;
        const int resources[] = {RLIMIT_CORE, RLIMIT_FSIZE, RLIMIT_NOFILE, RLIMIT_CPU};
        const rlim_t expected[] = {0, 0, 128, 1};
        for (unsigned int index = 0; index < 4; ++index) {
            if (getrlimit(resources[index], &limits) != 0 || limits.rlim_cur != expected[index]) return 3;
        }
    }
    printf("synthetic-ok\n");
    return 0;
}
