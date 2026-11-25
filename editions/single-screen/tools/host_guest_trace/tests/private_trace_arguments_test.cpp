#include "private_trace_arguments.h"
#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

using namespace recomp::private_trace;
const std::string root="/data/user/0/dev.recompbootstrap.smoke/files/private-trace";
const std::string id="0123456789abcdef0123456789abcdef";
std::vector<std::uint8_t> encode(const std::vector<std::string>& values){
    std::vector<std::uint8_t> data;
    for(const auto& value:values){data.insert(data.end(),value.begin(),value.end());data.push_back(0);}
    return data;
}
int main(){
    const auto run=root+"/run-"+id+"/";
    std::vector<std::string> base{run+"host_guest_trace",run+"decoded.image.bin","2181038080","100","0","1","2","3","4","5","6",
        "--read-only-file","151",root+"/inputs/abc.bin","--experimental-owned-color-program",root+"/modules/boot"};
    auto blob=encode(base);
    std::vector<std::string> output{"unchanged"};
    assert(parseArguments(blob.data(),blob.size(),root,id,output) && output==base);
    std::size_t rejected=0;
    const auto reject=[&](const std::vector<std::uint8_t>& input,std::string_view directory,std::string_view request){
        output={"preserved"};
        assert(!parseArguments(input.data(),input.size(),directory,request,output));
        assert(output==std::vector<std::string>{"preserved"});++rejected;
    };
    reject({},root,id);
    auto changed=blob;changed.pop_back();reject(changed,root,id);
    changed=blob;changed.push_back(0);reject(changed,root,id);
    for(const auto& directory:{"relative","/bad/../root","/bad//root","/bad/./root","/bad/","/bad\\root"})reject(blob,directory,id);
    for(std::size_t at=0;at<id.size();++at){auto bad=id;bad[at]='g';reject(blob,root,bad);}
    reject(blob,root,id.substr(1));reject(blob,root,id+"0");
    for(const auto& path:{"/etc/passwd","../input","foo/bar","foo\\bar","/data/user/0/foreign/file"}){
        auto values=base;values.back()=path;reject(encode(values),root,id);
    }
    for(const auto& suffix:{"inputs/../bad","inputs/./bad","inputs//bad","inputs/","inputs2/bad",
            "modules/../inputs/bad","run-ffffffffffffffffffffffffffffffff/bad","private-trace-library.so","inputs/a b"}){
        auto values=base;values.back()=root+"/"+suffix;reject(encode(values),root,id);
    }
    for(std::size_t index=0;index<2;++index){auto values=base;values[index]=root+"/inputs/other";reject(encode(values),root,id);}
    auto shortArgs=base;shortArgs.resize(10);reject(encode(shortArgs),root,id);
    for(unsigned byte=1;byte<256;++byte){
        if(byte>=32 && byte<=126)continue;
        auto values=base;values[2]=std::string(1,static_cast<char>(byte));reject(encode(values),root,id);
    }
    auto values=base;values[2]=std::string(4096,'x');auto valid=encode(values);
    assert(parseArguments(valid.data(),valid.size(),root,id,output) && output==values);
    values[2]+='x';reject(encode(values),root,id);
    values=base;values.resize(MaximumArguments,"0");valid=encode(values);
    assert(parseArguments(valid.data(),valid.size(),root,id,output) && output==values);
    values.push_back("0");reject(encode(values),root,id);
    values=base;while(encode(values).size()<MaximumArgumentBytes-4097)values.push_back(std::string(4096,'x'));
    auto size=encode(values).size();values.push_back(std::string(MaximumArgumentBytes-size-1,'x'));valid=encode(values);
    assert(valid.size()==MaximumArgumentBytes && parseArguments(valid.data(),valid.size(),root,id,output));
    valid.insert(valid.end()-1,'x');reject(valid,root,id);
    assert(!parseArguments(nullptr,16,root,id,output));
    // Borrowed bytes inside output stay alive until the success swap.
    output={std::string(reinterpret_cast<const char*>(blob.data()),blob.size())};
    assert(parseArguments(reinterpret_cast<const std::uint8_t*>(output[0].data()),output[0].size(),root,id,output));
    assert(output==base);
    std::printf("PRIVATE_TRACE_ARGUMENTS_PASS: %zu rejection controls; syntax/location only, no guest execution\n",rejected);
}
