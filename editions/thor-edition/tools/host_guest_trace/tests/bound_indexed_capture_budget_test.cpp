#include "bound_indexed_capture_budget.h"
#include <cassert>
#include <cstdio>
#include <limits>

int main() {
    using Budget=BoundIndexedCaptureBudget;
    Budget b;
    assert(!b.available(0,false) && !b.available(0,true));
    const auto perRecord=Budget::RoutineBytes/Budget::RoutineRecords;
    for(unsigned sequence=1;sequence<=Budget::RoutineRecords;++sequence)
        assert(b.retain(sequence,false,perRecord));
    assert(b.retainedBytes()==Budget::RoutineBytes);
    assert(!b.available(128,false) && !b.available(129,false));
    assert(!b.retain(129,false,1));
    assert(b.available(129,true)==Budget::FailureReserve);
    assert(!b.retain(129,true,Budget::FailureReserve+1));
    assert(!b.retain(129,true,std::numeric_limits<std::size_t>::max()));
    assert(b.retain(129,true,Budget::FailureReserve));
    assert(b.retainedBytes()==Budget::TotalBytes);
    assert(b.failureBytes()==Budget::FailureReserve);
    assert(!b.available(130,true) && !b.retain(130,true,1));
    assert(!b.retain(1,false,1));
    b={};
    assert(b.retain(1,true,100));
    assert(!b.available(2,true));
    assert(b.available(2,false)==Budget::RoutineBytes);
    assert(b.retain(2,false,Budget::RoutineBytes));
    assert(b.retainedBytes()==Budget::RoutineBytes+100);
    b={};
    assert(b.available(std::numeric_limits<unsigned>::max(),true)==Budget::FailureReserve);
    assert(!b.available(std::numeric_limits<unsigned>::max(),false));
    assert(!b.retain(1,true,0));
    std::puts("BOUND_INDEXED_CAPTURE_BUDGET_PASS history128_full=1 exception129_reserved=1 total32MiB=1 second_exception_rejected=1 reset=1 gpu_work=0");
}
