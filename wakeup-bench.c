#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

typedef struct { uint32_t next; unsigned char padding[60]; } Node;
_Static_assert(sizeof(Node)==64,"One cache line per node");
static volatile sig_atomic_t stopping;
static volatile uint32_t sink;
static void stop(int sig) { (void)sig; stopping=1; }
static uint64_t ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec; }
static uint64_t random64(uint64_t *seed) { uint64_t x=*seed; x^=x>>12; x^=x<<25; x^=x>>27; *seed=x; return x*2685821657736338717ull; }
static int order(const void *a,const void *b) { double x=*(const double*)a,y=*(const double*)b; return (x>y)-(x<y); }
static double quantile(double *values,int count,double q) { int index=(int)((count-1)*q); return values[index]; }

int main(int argc,char **argv) {
    if(argc!=4) { fprintf(stderr,"Usage: idle_memory_latency qos_us(-1=none) samples(1..2000) tight_timer_slack(0|1)\n"); return 2; }
    char *end=NULL; long qos=strtol(argv[1],&end,10); if(!end || *end || (qos!=-1 && qos!=1)) return 2;
    long samples=strtol(argv[2],&end,10); if(!end || *end || samples<1 || samples>2000) return 2;
    long tight_slack=strtol(argv[3],&end,10); if(!end || *end || (tight_slack!=0 && tight_slack!=1)) return 2;
    signal(SIGINT,stop); signal(SIGTERM,stop); signal(SIGHUP,stop);
    int status=1,fd=-1; Node *nodes=MAP_FAILED; uint32_t *permutation=NULL;
    double *wake=NULL,*access=NULL,*finish=NULL,*first=NULL;
    const size_t bytes=256ul*1024*1024,align=2ul*1024*1024;
    const uint32_t count=(uint32_t)(bytes/sizeof(Node)),steps=512;
    const uint64_t period=5000000ull;
    fd=open("/dev/cpu_dma_latency",O_RDWR|O_CLOEXEC);
    if(fd<0) { perror("open latency QoS"); goto cleanup; }
    int32_t original=-1;
    if(read(fd,&original,sizeof(original))!=sizeof(original)) { perror("read QoS"); goto cleanup; }
    if(qos==1) { int32_t limit=1; if(write(fd,&limit,sizeof(limit))!=sizeof(limit)) { perror("write QoS"); goto cleanup; } }
    long original_slack=prctl(PR_GET_TIMERSLACK,0UL,0UL,0UL,0UL);
    if(original_slack<0) { perror("get timer slack"); goto cleanup; }
    if(tight_slack && prctl(PR_SET_TIMERSLACK,1UL,0UL,0UL,0UL)!=0) { perror("set timer slack"); goto cleanup; }
    void *mapping=mmap(NULL,bytes+align,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(mapping==MAP_FAILED) { perror("mmap"); goto cleanup; }
    uintptr_t address=((uintptr_t)mapping+align-1)&~(uintptr_t)(align-1);
    size_t prefix=address-(uintptr_t)mapping,suffix=bytes+align-prefix-bytes;
    if(prefix) munmap(mapping,prefix);
    if(suffix) munmap((void*)(address+bytes),suffix);
    nodes=(Node*)address;
    if(madvise(nodes,bytes,MADV_HUGEPAGE)!=0) { perror("madvise"); goto cleanup; }
    memset(nodes,0,bytes);
    permutation=malloc((size_t)count*sizeof(*permutation));
    wake=malloc((size_t)samples*sizeof(double)); access=malloc((size_t)samples*sizeof(double)); finish=malloc((size_t)samples*sizeof(double)); first=malloc((size_t)samples*sizeof(double));
    if(!permutation || !wake || !access || !finish || !first) goto cleanup;
    for(int i=0;i<samples;i++) { ((volatile double*)wake)[i]=0;((volatile double*)access)[i]=0;((volatile double*)finish)[i]=0;((volatile double*)first)[i]=0; }
    for(uint32_t i=0;i<count;i++) permutation[i]=i;
    uint64_t seed=0xcafe20261026ull;
    for(uint32_t i=count-1;i>0;i--) { uint32_t j=(uint32_t)(random64(&seed)%(i+1)); uint32_t v=permutation[i]; permutation[i]=permutation[j]; permutation[j]=v; }
    for(uint32_t i=0;i<count;i++) nodes[permutation[i]].next=permutation[(i+1)%count];
    uint32_t cursor=permutation[0]; free(permutation); permutation=NULL;
    printf("{\"event\":\"ready\",\"cpu\":%d,\"qos_us\":%ld,\"initial_global_qos_us\":%d,\"original_timer_slack_ns\":%ld,\"effective_timer_slack_ns\":%ld,\"bytes\":%zu,\"period_us\":5000,\"loads_per_wakeup\":%u}\n",sched_getcpu(),qos,original,original_slack,(long)prctl(PR_GET_TIMERSLACK,0UL,0UL,0UL,0UL),bytes,steps); fflush(stdout);
    struct timespec settle={2,0}; nanosleep(&settle,NULL);
    struct rusage before,after; getrusage(RUSAGE_SELF,&before);
    uint64_t deadline=ns()+period; int measured=0,missed=0;
    for(int i=-50;i<samples && !stopping;i++) {
        if(i==0) getrusage(RUSAGE_SELF,&before);
        struct timespec absolute={(time_t)(deadline/1000000000ull),(long)(deadline%1000000000ull)};
        int result;
        do { result=clock_nanosleep(CLOCK_MONOTONIC,TIMER_ABSTIME,&absolute,NULL); } while(result==EINTR && !stopping);
        if(stopping) break;
        if(result) { errno=result; perror("clock_nanosleep"); goto cleanup; }
        uint64_t woke=ns();
        cursor=nodes[cursor].next;
        uint64_t one=ns();
        for(uint32_t k=1;k<steps;k++) cursor=nodes[cursor].next;
        uint64_t completed=ns(); sink=cursor;
        if(i>=0) { wake[i]=(woke-deadline)/1000.; first[i]=(one-woke); access[i]=(completed-woke)/(double)steps; finish[i]=(completed-deadline)/1000.; measured++; }
        deadline+=period;
        if(completed>=deadline) { missed++; deadline=completed+period; }
    }
    getrusage(RUSAGE_SELF,&after);
    if(stopping || measured!=samples) { fprintf(stderr,"Interrupted; not a complete case\n"); goto cleanup; }
    qsort(wake,measured,sizeof(double),order); qsort(access,measured,sizeof(double),order); qsort(finish,measured,sizeof(double),order); qsort(first,measured,sizeof(double),order);
    double mean_wake=0,mean_access=0,mean_finish=0;
    for(int i=0;i<measured;i++) { mean_wake+=wake[i]/measured; mean_access+=access[i]/measured; mean_finish+=finish[i]/measured; }
    printf("{\"event\":\"complete\",\"samples\":%d,\"qos_us\":%ld,\"wake_mean_us\":%.4f,\"wake_p50_us\":%.4f,\"wake_p95_us\":%.4f,\"wake_p99_us\":%.4f,\"wake_max_us\":%.4f,\"batch_mean_ns_per_load\":%.4f,\"batch_p50_ns_per_load\":%.4f,\"batch_p99_ns_per_load\":%.4f,\"first_load_plus_clock_p50_ns\":%.4f,\"deadline_to_finish_mean_us\":%.4f,\"deadline_to_finish_p99_us\":%.4f,\"deadline_to_finish_max_us\":%.4f,\"minor_faults\":%ld,\"major_faults\":%ld,\"missed_periods\":%d,\"final_cursor\":%u}\n",measured,qos,mean_wake,quantile(wake,measured,.5),quantile(wake,measured,.95),quantile(wake,measured,.99),wake[measured-1],mean_access,quantile(access,measured,.5),quantile(access,measured,.99),quantile(first,measured,.5),mean_finish,quantile(finish,measured,.99),finish[measured-1],after.ru_minflt-before.ru_minflt,after.ru_majflt-before.ru_majflt,missed,cursor); fflush(stdout);
    status=0;
cleanup:
    if(fd>=0) close(fd);
    if(nodes!=MAP_FAILED) munmap(nodes,bytes);
    free(permutation);free(wake);free(access);free(finish);free(first);
    return status;
}
