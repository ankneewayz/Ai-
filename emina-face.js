const eminaFace=document.getElementById('eminaFace');
let emotion='neutral';

const eyeLeft=document.createElement('div');
const eyeRight=document.createElement('div');

[eyeLeft,eyeRight].forEach(eye=>{
  eye.style.width='40px';
  eye.style.height='40px';
  eye.style.background='cyan';
  eye.style.borderRadius='50%';
  eye.style.position='absolute';
  eye.style.top='70px';
  eye.style.transition='background 0.3s';
  eminaFace.appendChild(eye);
});
eyeLeft.style.left='50px';
eyeRight.style.left='110px';

// Head tilt & idle breathing
let tiltAngle=0, tiltDirection=1, scaleY=1;
function animateFace(){
  tiltAngle+=tiltDirection*0.05;
  if(tiltAngle>3||tiltAngle<-3) tiltDirection*=-1;
  scaleY=1+0.01*Math.sin(Date.now()/300);
  eminaFace.style.transform=`rotate(${tiltAngle}deg) scaleY(${scaleY})`;
  requestAnimationFrame(animateFace);
}
animateFace();

// Blinking
function blinkEyes(){
  [eyeLeft,eyeRight].forEach(eye=>{
    eye.style.height='5px';
    setTimeout(()=>eye.style.height='40px',200);
  });
}
setInterval(blinkEyes, Math.random()*4000+3000);

// Cursor follow
document.addEventListener('mousemove',e=>{
  const rect=eminaFace.getBoundingClientRect();
  const cx=rect.left+rect.width/2;
  const cy=rect.top+rect.height/2;
  const dx=(e.clientX-cx)/20;
  const dy=(e.clientY-cy)/20;
  eyeLeft.style.transform=`translate(${dx}px, ${dy}px)`;
  eyeRight.style.transform=`translate(${dx}px, ${dy}px)`;
});

// Emotion colors
window.updateFace=(newEmotion)=>{
  emotion=newEmotion;
  let color='cyan';
  switch(emotion){
    case'happy':color='pink';break;
    case'sad':color='blue';break;
    case'thinking':color='yellow';break;
    case'neutral':color='cyan';break;
  }
  [eyeLeft,eyeRight].forEach(eye=>eye.style.background=color);
};

// Idle eye movement
let lastMouseTime=Date.now();
document.addEventListener('mousemove',()=>lastMouseTime=Date.now());
function idleEyeMovement(){
  const idleTime=Date.now()-lastMouseTime;
  if(idleTime>3000){
    const dx=Math.sin(Date.now()/500)*5;
    const dy=Math.sin(Date.now()/400)*3;
    eyeLeft.style.transform=`translate(${dx}px,${dy}px)`;
    eyeRight.style.transform=`translate(${dx}px,${dy}px)`;
  }
  requestAnimationFrame(idleEyeMovement);
}
idleEyeMovement();