#include "stopped_submission_capture.h"
#include "guest_execution_guard.h"
#include <cassert>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <csignal>
#include <sys/resource.h>

using namespace recomp;
static std::vector<std::uint8_t> bytes(const std::vector<std::uint32_t>& words) {
    std::vector<std::uint8_t> out;for(auto w:words)for(unsigned b=0;b<4;++b)out.push_back(w>>(24-8*b));return out;
}
static gpu::Pm4Snapshot snapshot(const std::vector<std::uint32_t>& words) {
    auto data=bytes(words);gpu::Pm4Snapshot out;gpu::Pm4Error error;
    assert(gpu::decodePm4(data.data(),data.size(),{},out,error));return out;
}
static std::string json(const GraphicsSubmissionDiagnostic& d) {std::ostringstream s;d.json(s);return s.str();}
int main(int argc,char** argv) {
    assert(argc==2 && std::filesystem::is_directory(argv[1]));
    // Reproduce the private Android launcher's actual hard limit on the host
    // too. A host-only unconstrained file roundtrip missed this exact failure.
    const rlimit fileLimit{65536,65536};assert(::setrlimit(RLIMIT_FSIZE,&fileLimit)==0);
    assert(std::signal(SIGXFSZ,SIG_IGN)!=SIG_ERR);
    auto path=(std::filesystem::path(argv[1])/"submission-test-XXXXXX").string();
    assert(::mkdtemp(path.data()));const std::filesystem::path directory=path;
    GraphicsSubmissionDiagnostic d;int calls=0;
    d.begin(100);
    const bool accepted=d.attempt("first",[&]{++calls;return false;}) ||
        d.attempt("second",[&]{++calls;return true;}) || d.attempt("must-not-run",[&]{++calls;return true;});
    d.finish(accepted);assert(accepted && calls==2 && json(d).find("must-not-run")==std::string::npos);
    d.begin(100);assert(!d.attempt("optional",[]{return std::optional<bool>{};}));
    const auto refused=d.attempt("optional-false",[]{return std::optional<bool>{false};});
    assert(refused.has_value() && !*refused);d.finish(false);
    assert(json(d).find("not-applicable")!=std::string::npos && json(d).find("returned-false")!=std::string::npos);
    // Connect the observer to the REAL graphics-write dispatch and execution
    // guard. Refusal/exception must remain unsupported scalar stops, not success.
    bootstrap::GuestExecutionGuard guard;
    for(unsigned mode=0;mode<3;++mode) {
        bootstrap::resetGuestGraphicsSubmission();calls=0;
        assert(bootstrap::bindGuestGraphicsSubmission([&](unsigned word) {
            d.begin(word);
            const bool result=d.attempt("guard-connected",[&]() -> bool {
                ++calls;if(mode==1)throw std::runtime_error("quoted\"\\\nerror");if(mode==2)throw 42;return false;
            });d.finish(result);return result;
        }));
        const auto execution=guard.run(100,[&] {
            if(!bootstrap::tryWriteGuestGraphicsSubmission(0x7FC80714,4,100))
                bootstrap::recomp_guest_execution_scalar_access({0x7FC80714,4,100,true,bootstrap::GuestScalarRegion::GpuRegisters});
        });
        assert(execution.reason==bootstrap::GuestExecutionStopReason::UnsupportedScalarAccess && calls==1);
        assert(json(d).find(mode==1?"std-exception":mode==2?"other-exception":"returned-false")!=std::string::npos);
        if(mode==1)assert(json(d).find("quoted\\\"\\\\ error")!=std::string::npos);
    }
    bootstrap::resetGuestGraphicsSubmission();d.begin(100);
    for(unsigned i=0;i<33;++i)assert(!d.attempt("bounded",[]{return false;}));
    d.finish(false);assert(json(d).find("\"truncated\":true")!=std::string::npos);
    d.begin(100);d.attempt("authored-rejection",[]{return false;});d.finish(false);

    // AUTHORED large graph has the observed word extents, NOT game payloads.
    std::vector<std::uint32_t> child(22320,0x80000000);
    const std::array<unsigned,3> counts{74,13,48};
    for(unsigned i=0;i<3;++i){child[3*i]=0xC0013F00;child[3*i+1]=0x20000+i*0x1000;child[3*i+2]=counts[i];}
    const auto root=snapshot({0xC0013F00,0x10000,22320});gpu::Pm4IndirectGraph graph;gpu::Pm4GraphError error;
    assert(gpu::inspectPm4IndirectGraph(root,[&](unsigned address,unsigned size)->std::optional<std::vector<std::uint8_t>> {
        if(address==0x10000 && size==child.size()*4)return bytes(child);
        for(unsigned i=0;i<3;++i)if(address==0x20000+i*0x1000 && size==counts[i]*4)
            return bytes(std::vector<std::uint32_t>(counts[i],0x80000000));
        return std::nullopt;
    },graph,error));
    assert(graph.totalWords==22458 && graph.nodes.size()==4);
    using Result=trace::StoppedSubmissionSave;
    assert(trace::validateStoppedSubmission(root,graph)==Result::Saved);
    unsigned negatives=0;
    const auto reject=[&](auto mutate,Result expected=Result::InvalidGraph){auto bad=graph;mutate(bad);
        assert(trace::validateStoppedSubmission(root,bad)==expected);++negatives;};
    reject([](auto& g){++g.totalWords;});reject([](auto& g){++g.totalPackets;});
    reject([](auto& g){g.nodes[0].parent=1;});reject([](auto& g){++g.nodes[0].physicalAddress;});
    reject([](auto& g){g.nodes[1].parentOffsetWords=1;});reject([](auto& g){g.nodes[0].depth=9;});
    reject([](auto& g){g.nodes.pop_back();});reject([](auto& g){g.nodes.push_back(g.nodes.back());});
    reject([](auto& g){g.nodes[0].snapshot={};});
    reject([](auto& g){g.totalWords=trace::StoppedSubmissionMaxWords+1;},Result::Limit);
    reject([](auto& g){g.nodes.resize(65);},Result::Limit);
    assert(trace::saveStoppedSubmission(directory,root,graph,97,100,d)==Result::Saved);
    assert(trace::saveStoppedSubmission(directory,root,graph,97,100,d)==Result::IoFailure);
    assert(trace::saveStoppedSubmission(directory/"absent",root,graph,97,100,d)==Result::IoFailure);
    const auto link=directory/"parent-link";std::filesystem::create_directory_symlink(directory,link);
    assert(trace::saveStoppedSubmission(link,root,graph,97,100,d)==Result::IoFailure);
    for(unsigned id=0;id<=graph.nodes.size();++id) {
        const auto expected=bytes(id?graph.nodes[id-1].snapshot.words():root.words());
        const auto parts=std::max(std::size_t{1},(expected.size()+65535)/65536);
        std::vector<std::uint8_t> actual;
        for(std::size_t part=0;part<parts;++part) {
            const auto name="node-"+std::to_string(id)+(parts==1?"":"-part-"+std::to_string(part))+".bin";
            std::ifstream file(directory/"stopped-submission"/name,std::ios::binary);
            assert(file.is_open());const std::vector<std::uint8_t> chunk((std::istreambuf_iterator<char>(file)),{});
            assert(chunk.size()==std::min(std::size_t{65536},expected.size()-part*65536));
            actual.insert(actual.end(),chunk.begin(),chunk.end());
        }
        assert(actual==expected);
    }
    for(const auto& file:std::filesystem::directory_iterator(directory/"stopped-submission"))
        assert(file.file_size()<=65536);
    assert(std::filesystem::is_regular_file(directory/"stopped-submission"/"dispatch.json"));
    std::cout<<"STOPPED_SUBMISSION_CAPTURE_PASS authored_words=22458 owned_roundtrip=1 guard_connection=3 rejection_controls="<<negatives
        <<" no_overwrite=1 symlink_rejected=1 short_circuit=1 file_limit=65536 chunked_roundtrip=1 GPU=0 gameplay=0\n";
    std::cout<<"PRIVATE_CAPTURE "<<(directory/"stopped-submission").string()<<'\n';
}
