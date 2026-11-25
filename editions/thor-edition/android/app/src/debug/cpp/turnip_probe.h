// turnip_probe.h (TURNIP PROBE TEST APP 2026-11-22, lane turnip item 5; carried into next-fold/next-thor 2026-11-24 for the lead's
// default-on test: built only with -DRECOMP_TURNIP=ON, which only debug builds pass; the loader decides when it runs, turnip_choice.h).
// Plan: local-analysis/audit-fixes-1116/opt/turnip/PLAN.md, "The exact first experiment" step 1.
//
// One read-only look at what a Turnip driver, loaded in THIS process (:private_trace) through libadrenotools, reports for
// device 0. Runs in runTrace() before the game library is opened, and only when files/private-trace/diag-turnip exists.
// Absent switch: this function returns at the access() check and nothing else differs from the standing loader.
//
// Every Vulkan call goes through function pointers taken from the adrenotools handle (vkGetInstanceProcAddr of the hooked
// libvulkan copy). The loader library also links the system libvulkan.so; a direct vk* call here would silently ask the
// Qualcomm driver, so none is made. The instance is destroyed afterwards; the adrenotools handle is NOT dlclosed (unloading
// a Vulkan driver/loader mid-process is a crash risk) - it stays mapped and unused. The game library keeps its own PLT
// imports bound to the system libvulkan.so, so the game itself runs on the system driver exactly as today.
//
// Log lines (tag RecompPrivateTrace):
//   TURNIP_PRE adreno_mapped=<0|1> vulkan_mapped=<0|1> hooks=<nativeLibraryDir> driver=<sha-less path> driver_present=<0|1>
//   TURNIP_OPEN result=handle|null|no-hook-dir|no-driver-file ms=<t>
//   TURNIP_PROBE driver_id=<n> driver_name=... api=<a.b.c> driver_version_raw=<n> vendor=<n> device_id=<n> descriptor_indexing=<0|1>
//       runtimeDescriptorArray=.. sampledImageArrayDynamicIndexing=.. shaderClipDistance=.. fragmentStoresAndAtomics=..
//       vertexPipelineStoresAndAtomics=.. samplerAnisotropy=.. sampleRateShading=.. independentBlend=.. gate9=<k>/9
//       maxBoundDescriptorSets=<n> device=<name>
//   TURNIP_PROBE_OPT scalarBlockLayout=.. occlusionQueryPrecise=.. depthBounds=.. bufferDeviceAddress=.. load_store_op_none=..
//       global_priority=.. memory_budget=.. mirror_clamp=.. buffer_device_address_ext=.. depth_bias_control=.. driver_info=...
//   TURNIP_FORMAT name=<fmt> vk=<n> linear=0x.. optimal=0x.. buffer=0x..   (one line per format the renderer branches on)
//   TURNIP_PROBE error=<what failed>                                     (instead of TURNIP_PROBE when a step fails)
//   TURNIP_DONE ms=<t> (the normal system-driver launch continues)
// Only driver_id=18 (VK_DRIVER_ID_MESA_TURNIP) proves Turnip: a non-null handle alone does not (adrenotools falls back to
// the system driver when the custom .so fails to load).
#pragma once
#include <adrenotools/driver.h>
#include <android/log.h>
#include <dlfcn.h>
#include <time.h>
#include <unistd.h>
#include <vulkan/vulkan.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace turnip_probe {
inline double nowMs(){timespec t{};clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e3+t.tv_nsec/1e6;}
#define TURNIP_LOG(...) __android_log_print(ANDROID_LOG_INFO,"RecompPrivateTrace",__VA_ARGS__)

// nativeLibraryDir without a Java change: the directory this loader library was loaded from (real files with
// useLegacyPackaging = true; a path inside the APK, "...apk!/lib/...", means the hooks cannot be loaded).
inline std::string ownLibraryDir(){
    Dl_info info{};
    if(!dladdr(reinterpret_cast<void*>(&ownLibraryDir),&info) || !info.dli_fname)return {};
    std::string p=info.dli_fname;const auto slash=p.rfind('/');
    return slash==std::string::npos?std::string{}:p.substr(0,slash+1);
}
inline bool mapped(const char* needle){
    FILE* f=std::fopen("/proc/self/maps","re");if(!f)return false;
    char line[1024];bool hit=false;
    while(!hit && std::fgets(line,sizeof line,f))hit=std::strstr(line,needle)!=nullptr;
    std::fclose(f);return hit;
}
inline void printable(char* s){for(;*s;++s)if(static_cast<unsigned char>(*s)<0x20 || static_cast<unsigned char>(*s)>0x7E)*s='?';}
inline bool offered(const std::vector<VkExtensionProperties>& all,const char* name){
    for(const auto& e:all)if(std::strcmp(e.extensionName,name)==0)return true;
    return false;
}

inline void probe(void* vk){
    const auto gipa=reinterpret_cast<PFN_vkGetInstanceProcAddr>(dlsym(vk,"vkGetInstanceProcAddr"));
    if(!gipa){TURNIP_LOG("TURNIP_PROBE error=no-vkGetInstanceProcAddr");return;}
    const auto createInstance=reinterpret_cast<PFN_vkCreateInstance>(gipa(VK_NULL_HANDLE,"vkCreateInstance"));
    const auto enumerateVersion=reinterpret_cast<PFN_vkEnumerateInstanceVersion>(gipa(VK_NULL_HANDLE,"vkEnumerateInstanceVersion"));
    if(!createInstance){TURNIP_LOG("TURNIP_PROBE error=no-vkCreateInstance");return;}
    uint32_t loaderVersion=VK_API_VERSION_1_0;if(enumerateVersion)enumerateVersion(&loaderVersion);
    VkApplicationInfo app{};app.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO;app.pApplicationName="FivePocket turnip probe";
    app.apiVersion=VK_API_VERSION_1_2; // the game's instance version (v1935 device.cpp inspect())
    VkInstanceCreateInfo info{};info.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;info.pApplicationInfo=&app;
    VkInstance instance=VK_NULL_HANDLE;
    const VkResult created=createInstance(&info,nullptr,&instance);
    if(created!=VK_SUCCESS){TURNIP_LOG("TURNIP_PROBE error=create-instance-%d loader=%u.%u.%u",int(created),
        VK_VERSION_MAJOR(loaderVersion),VK_VERSION_MINOR(loaderVersion),VK_VERSION_PATCH(loaderVersion));return;}
#define TURNIP_FN(type,name) const auto name=reinterpret_cast<type>(gipa(instance,#name))
    TURNIP_FN(PFN_vkDestroyInstance,vkDestroyInstance);
    TURNIP_FN(PFN_vkEnumeratePhysicalDevices,vkEnumeratePhysicalDevices);
    TURNIP_FN(PFN_vkGetPhysicalDeviceProperties,vkGetPhysicalDeviceProperties);
    TURNIP_FN(PFN_vkGetPhysicalDeviceFeatures,vkGetPhysicalDeviceFeatures);
    TURNIP_FN(PFN_vkEnumerateDeviceExtensionProperties,vkEnumerateDeviceExtensionProperties);
    TURNIP_FN(PFN_vkGetPhysicalDeviceFormatProperties,vkGetPhysicalDeviceFormatProperties);
    TURNIP_FN(PFN_vkGetPhysicalDeviceFeatures2,vkGetPhysicalDeviceFeatures2);
    TURNIP_FN(PFN_vkGetPhysicalDeviceProperties2,vkGetPhysicalDeviceProperties2);
#undef TURNIP_FN
    if(!vkDestroyInstance){TURNIP_LOG("TURNIP_PROBE error=no-vkDestroyInstance");return;} // leak one instance rather than guess
    do{
        if(!vkEnumeratePhysicalDevices || !vkGetPhysicalDeviceProperties || !vkGetPhysicalDeviceFeatures ||
                !vkEnumerateDeviceExtensionProperties || !vkGetPhysicalDeviceFormatProperties){
            TURNIP_LOG("TURNIP_PROBE error=missing-core-entry-points");break;}
        uint32_t count=0;
        if(vkEnumeratePhysicalDevices(instance,&count,nullptr)!=VK_SUCCESS || count==0 || count>16){
            TURNIP_LOG("TURNIP_PROBE error=no-device count=%u",count);break;}
        std::vector<VkPhysicalDevice> devices(count);
        if(vkEnumeratePhysicalDevices(instance,&count,devices.data())!=VK_SUCCESS || count==0){TURNIP_LOG("TURNIP_PROBE error=no-device");break;}
        const VkPhysicalDevice physical=devices[0]; // the game's rule: device zero, no heuristic choice
        VkPhysicalDeviceProperties properties{};vkGetPhysicalDeviceProperties(physical,&properties);
        std::vector<VkExtensionProperties> exts;uint32_t n=0;
        if(vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,nullptr)==VK_SUCCESS && n && n<4096){
            exts.resize(n);if(vkEnumerateDeviceExtensionProperties(physical,nullptr,&n,exts.data())!=VK_SUCCESS)exts.clear();
            if(n<exts.size())exts.resize(n);}
        const bool v11=properties.apiVersion>=VK_API_VERSION_1_1, v12=properties.apiVersion>=VK_API_VERSION_1_2;
        const bool indexingExt=offered(exts,VK_EXT_DESCRIPTOR_INDEXING_EXTENSION_NAME);
        const bool indexingKnown=v12 || indexingExt;
        const bool driverPropsKnown=v12 || offered(exts,VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME);
        // Driver identity (core 1.2 / VK_KHR_driver_properties); a 1.1 device without the extension reports driver_id=0.
        VkPhysicalDeviceDriverProperties driver{};driver.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES;
        if(v11 && vkGetPhysicalDeviceProperties2 && driverPropsKnown){
            VkPhysicalDeviceProperties2 p2{};p2.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;p2.pNext=&driver;
            vkGetPhysicalDeviceProperties2(physical,&p2);}
        // Features: the gate's structures chained only where the device knows them (gpu_check_jni.cpp / device.cpp order).
        VkPhysicalDeviceDescriptorIndexingFeatures indexing{};indexing.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_INDEXING_FEATURES;
        VkPhysicalDeviceScalarBlockLayoutFeatures scalar{};scalar.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SCALAR_BLOCK_LAYOUT_FEATURES;
        VkPhysicalDeviceBufferDeviceAddressFeatures address{};address.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES;
        VkPhysicalDeviceFeatures2 all{};all.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
        void** tail=&all.pNext;
        if(indexingKnown){*tail=&indexing;tail=&indexing.pNext;}
        if(v12 || offered(exts,VK_EXT_SCALAR_BLOCK_LAYOUT_EXTENSION_NAME)){*tail=&scalar;tail=&scalar.pNext;}
        if(v12 || offered(exts,VK_KHR_BUFFER_DEVICE_ADDRESS_EXTENSION_NAME)){*tail=&address;tail=&address.pNext;}
        if(v11 && vkGetPhysicalDeviceFeatures2)vkGetPhysicalDeviceFeatures2(physical,&all);
        else vkGetPhysicalDeviceFeatures(physical,&all.features);
        const VkPhysicalDeviceFeatures& f=all.features;
        const unsigned gate[9]={unsigned(indexingKnown),unsigned(indexing.runtimeDescriptorArray),
            unsigned(f.shaderSampledImageArrayDynamicIndexing),unsigned(f.shaderClipDistance),unsigned(f.fragmentStoresAndAtomics),
            unsigned(f.vertexPipelineStoresAndAtomics),unsigned(f.samplerAnisotropy),unsigned(f.sampleRateShading),unsigned(f.independentBlend)};
        unsigned gateOk=0;for(unsigned g:gate)gateOk+=g?1u:0u;
        char name[VK_MAX_PHYSICAL_DEVICE_NAME_SIZE+1]{};std::memcpy(name,properties.deviceName,VK_MAX_PHYSICAL_DEVICE_NAME_SIZE);printable(name);
        char dname[VK_MAX_DRIVER_NAME_SIZE+1]{};std::memcpy(dname,driver.driverName,VK_MAX_DRIVER_NAME_SIZE);printable(dname);
        char dinfo[VK_MAX_DRIVER_INFO_SIZE+1]{};std::memcpy(dinfo,driver.driverInfo,VK_MAX_DRIVER_INFO_SIZE);printable(dinfo);
        for(char* c=dname;*c;++c)if(*c==' ')*c='_'; // keep key=value parsing simple; the device name stays last and keeps spaces
        TURNIP_LOG("TURNIP_PROBE driver_id=%u driver_name=%s api=%u.%u.%u driver_version_raw=%u vendor=%u device_id=%u loader=%u.%u.%u "
            "descriptor_indexing=%u runtimeDescriptorArray=%u sampledImageArrayDynamicIndexing=%u shaderClipDistance=%u "
            "fragmentStoresAndAtomics=%u vertexPipelineStoresAndAtomics=%u samplerAnisotropy=%u sampleRateShading=%u "
            "independentBlend=%u gate9=%u/9 maxBoundDescriptorSets=%u conformance=%u.%u.%u.%u device=%s",
            unsigned(driver.driverID),dname[0]?dname:"-",VK_VERSION_MAJOR(properties.apiVersion),VK_VERSION_MINOR(properties.apiVersion),
            VK_VERSION_PATCH(properties.apiVersion),properties.driverVersion,properties.vendorID,properties.deviceID,
            VK_VERSION_MAJOR(loaderVersion),VK_VERSION_MINOR(loaderVersion),VK_VERSION_PATCH(loaderVersion),
            gate[0],gate[1],gate[2],gate[3],gate[4],gate[5],gate[6],gate[7],gate[8],gateOk,properties.limits.maxBoundDescriptorSets,
            driver.conformanceVersion.major,driver.conformanceVersion.minor,driver.conformanceVersion.subminor,driver.conformanceVersion.patch,name);
        TURNIP_LOG("TURNIP_PROBE_OPT scalarBlockLayout=%u occlusionQueryPrecise=%u depthBounds=%u bufferDeviceAddress=%u "
            "load_store_op_none=%u global_priority=%u memory_budget=%u mirror_clamp=%u buffer_device_address_ext=%u "
            "depth_bias_control=%u extensions=%zu maxStorageBufferRange=%u maxMemoryAllocationCount=%u driver_info=%s",
            unsigned(scalar.scalarBlockLayout),unsigned(f.occlusionQueryPrecise),unsigned(f.depthBounds),unsigned(address.bufferDeviceAddress),
            unsigned(offered(exts,"VK_EXT_load_store_op_none") || offered(exts,"VK_KHR_load_store_op_none")),
            unsigned(offered(exts,"VK_EXT_global_priority") || offered(exts,"VK_KHR_global_priority")),
            unsigned(offered(exts,"VK_EXT_memory_budget")),unsigned(v12 || offered(exts,"VK_KHR_sampler_mirror_clamp_to_edge")),
            unsigned(offered(exts,"VK_KHR_buffer_device_address")),unsigned(offered(exts,"VK_EXT_depth_bias_control")),
            exts.size(),properties.limits.maxStorageBufferRange,properties.limits.maxMemoryAllocationCount,dinfo[0]?dinfo:"-");
        // Format answers the renderer branches on (generic_renderer.cpp vertexFormatSupported / hostFeatures / blitFilterable,
        // native_texture_image.cpp linear filter, direct_present.h R32_UINT). Raw feature masks, decoded on the Mac.
        struct Fmt{const char* name;VkFormat format;};
        static const Fmt formats[]={
            {"BC1_RGBA_UNORM",VK_FORMAT_BC1_RGBA_UNORM_BLOCK},{"BC2_UNORM",VK_FORMAT_BC2_UNORM_BLOCK},{"BC3_UNORM",VK_FORMAT_BC3_UNORM_BLOCK},
            {"BC4_UNORM",VK_FORMAT_BC4_UNORM_BLOCK},{"BC5_UNORM",VK_FORMAT_BC5_UNORM_BLOCK},
            {"R32_SFLOAT",VK_FORMAT_R32_SFLOAT},{"R32_UINT",VK_FORMAT_R32_UINT},
            {"R8G8B8A8_USCALED",VK_FORMAT_R8G8B8A8_USCALED},{"R8G8B8A8_SSCALED",VK_FORMAT_R8G8B8A8_SSCALED},
            {"R16G16_USCALED",VK_FORMAT_R16G16_USCALED},{"R16G16_SSCALED",VK_FORMAT_R16G16_SSCALED},
            {"R16G16B16A16_USCALED",VK_FORMAT_R16G16B16A16_USCALED},{"R16G16B16A16_SSCALED",VK_FORMAT_R16G16B16A16_SSCALED},
            {"A2B10G10R10_USCALED",VK_FORMAT_A2B10G10R10_USCALED_PACK32},{"A2B10G10R10_SSCALED",VK_FORMAT_A2B10G10R10_SSCALED_PACK32},
            {"D24_UNORM_S8_UINT",VK_FORMAT_D24_UNORM_S8_UINT},{"D32_SFLOAT_S8_UINT",VK_FORMAT_D32_SFLOAT_S8_UINT},
            {"R16G16B16A16_SFLOAT",VK_FORMAT_R16G16B16A16_SFLOAT}};
        for(const auto& x:formats){VkFormatProperties p{};vkGetPhysicalDeviceFormatProperties(physical,x.format,&p);
            TURNIP_LOG("TURNIP_FORMAT name=%s vk=%d linear=0x%x optimal=0x%x buffer=0x%x",x.name,int(x.format),
                unsigned(p.linearTilingFeatures),unsigned(p.optimalTilingFeatures),unsigned(p.bufferFeatures));}
    }while(false);
    vkDestroyInstance(instance,nullptr);
}

// The whole probe. files = the app's files directory (runTrace's path). Returns nothing; never throws past here.
// TURNIP GAME A/B (2026-11-22, qol-turnip2): returns the adrenotools handle (nullptr: the system driver stays); the instance probe runs
// only with files/private-trace/diag-turnip-probe as well. The handle is then handed to the game library (private_trace_jni.cpp).
// 2026-11-24 (default-on path): the loader has already chosen Turnip (turnip_choice.h); no switch is read here. The driver is the one
// packaged in the app (beside the hook libraries in nativeLibraryDir), else files/turnip/ (the earlier test copies' pushed file).
inline void* runIfAsked(const std::string& files){
    const double t0=nowMs();
    const std::string hooks=ownLibraryDir();
    const bool packaged=!hooks.empty() && ::access((hooks+"libvulkan_freedreno.so").c_str(),R_OK)==0;
    const std::string driverDir=packaged?hooks:files+"/turnip/"; // must end in '/': adrenotools concatenates dir + name
    const bool present=::access((driverDir+"libvulkan_freedreno.so").c_str(),R_OK)==0;
    TURNIP_LOG("TURNIP_PRE adreno_mapped=%d vulkan_mapped=%d hooks=%s driver=%slibvulkan_freedreno.so driver_present=%d",
        int(mapped("vulkan.adreno.so")),int(mapped("libvulkan.so")),hooks.empty()?"-":hooks.c_str(),driverDir.c_str(),int(present));
    if(hooks.empty() || hooks.find("!/")!=std::string::npos){TURNIP_LOG("TURNIP_OPEN result=no-hook-dir");return nullptr;}
    if(!present){TURNIP_LOG("TURNIP_OPEN result=no-driver-file");return nullptr;}
    void* handle=nullptr;
    try{
        void* vk=adrenotools_open_libvulkan(RTLD_NOW|RTLD_LOCAL,ADRENOTOOLS_DRIVER_CUSTOM,nullptr,hooks.c_str(),driverDir.c_str(),
            "libvulkan_freedreno.so",nullptr,nullptr);
        TURNIP_LOG("TURNIP_OPEN result=%s ms=%.1f",vk?"handle":"null",nowMs()-t0);
        if(vk && ::access((files+"/private-trace/diag-turnip-probe").c_str(),F_OK)==0)probe(vk); // the handle is kept (never dlclosed)
        handle=vk;
    }catch(...){TURNIP_LOG("TURNIP_PROBE error=exception");handle=nullptr;}
    TURNIP_LOG("TURNIP_DONE ms=%.1f adreno_mapped_after=%d turnip_mapped_after=%d",nowMs()-t0,int(mapped("vulkan.adreno.so")),
        int(mapped("libvulkan_freedreno.so")));
    return handle;
}
#undef TURNIP_LOG
}  // namespace turnip_probe
